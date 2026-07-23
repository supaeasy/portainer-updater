**[English](README.md) | Deutsch**

# Portainer Updater

Ersetzt das manuelle Durchklicken aller Portainer-Stacks: erkennt verfuegbare
Image-Updates, laesst Claude die Release-Notes zwischen aktueller und neuer
Version auf Breaking Changes und noetige `docker-compose.yml`-Anpassungen
pruefen (z.B. gepinnte Sub-Versionen wie bei immich), und zeigt alles in einer
Uebersicht mit Checkboxen an. Ausgewaehlte Stacks werden per Portainer-API
aktualisiert und redeployed.

## Architektur

```
┌──────┐   Update erkannt    ┌──────────────────┐   Releases    ┌────────┐
│ WUD  │ ──────────────────▶ │  analysis-layer   │ ────────────▶ │ GitHub │
│      │  (http-Trigger)     │  (FastAPI)        │               └────────┘
└──────┘                     │                   │   compose.yml  ┌───────────┐
   ▲                         │                   │ ─────────────▶ │ Portainer │
   │ liest nur                │                   │ ◀───────────── │    API    │
   │ docker.sock (ro)         │  Claude-Analyse   │  Redeploy       └───────────┘
   └─────────────────────────  + SQLite-Speicher  │
                               │  + Dashboard-UI   │
                               └──────────────────┘
                                        ▲
                                        │ Browser (Checkboxen, "aktualisieren")
```

- **WUD** (`getwud/wud`) beobachtet alle laufenden Container per (read-only)
  Docker-Socket und meldet neue Image-Versionen. WUD selbst aktualisiert
  nichts - das macht bewusst niemand automatisch ohne Blick auf die Analyse.
- **analysis-layer** ist der eigentliche Baustein aus diesem Repo: nimmt
  WUD-Meldungen entgegen, holt die GitHub-Release-Notes zwischen alter und
  neuer Version, holt die aktuelle compose-Datei des betroffenen Stacks direkt
  aus Portainer, und laesst Claude eine Einschaetzung (Risiko, Klartext-
  Zusammenfassung, noetige compose-Aenderungen inkl. Formulierungsvorschlag)
  erstellen. Ergebnis landet in SQLite und im Dashboard.
- **Dashboard** (unter `/`, vom analysis-layer mit ausgeliefert): Liste aller
  offenen Updates mit Risiko-Einschaetzung, Checkboxen, und einem Button
  "Ausgewaehlte aktualisieren".

### Warum kein Webhook pro Stack?

Portainer bringt fertige Stack-Webhooks mit (in Business Edition inkl.
`tag=`/`pullimage=`-Query-Parametern, um beim Ausloesen ein bestimmtes Tag
zu erzwingen). Trotzdem nutzt der analysis-layer stattdessen die
Portainer-REST-API direkt (`GET/PUT /api/stacks/{id}`, funktioniert auch in
der Community Edition - die Wahl haengt also nicht an der Edition): ein
Webhook kann immer nur ein einzelnes, vorher als Variable parametrisiertes
Tag ersetzen. Bei Stacks, die mehrere Komponenten mit fest im Klartext
gepinnten Versionen enthalten (immich: App-Image, ML-Image, Postgres/vectors-
Image jeweils einzeln gepinnt), reicht das nicht - da muss die compose-Datei
selbst an mehreren Stellen editiert werden, und das kann nur die API. Sie
liest die compose-Datei, setzt bei Bedarf den von Claude vorgeschlagenen
Patch ein, und redeployed den Stack mit `RepullImageAndRedeploy`.

**Bonus mit Business Edition:** unter *Host -> Setup* (Docker Standalone) bzw.
*Environment -> Setup* laesst sich "Show an image(s) up to date indicator for
Stacks, Services and Containers" aktivieren - ein einfacher gruen/orange-Haken
direkt in der Portainer-UI (Digest-Vergleich, kein Versions-/Breaking-Change-
Kontext). Nettes ergaenzendes Signal, ersetzt aber nicht das Dashboard hier.

## Setup

### 1. Portainer-API-Key anlegen

Portainer UI -> User settings -> Access tokens -> Add access token.

**Empfehlung:** dafuer einen eigenen, eingeschraenkten Portainer-Benutzer
anlegen, der per RBAC nur auf die Environments zugreifen darf, die dieses Tool
verwalten soll - nicht den Admin-Account. Der Key kann sonst *jeden* Stack in
Portainer veraendern.

### 2. Dateien auf den Host kopieren

Dieser Stack wird per Bind-Mounts deployed (Portainer "Web editor", nicht die
Git-Deployment-Methode) - `analysis-layer/` muss deshalb physisch auf dem Host
liegen: Docker braucht es dort fuer den Build-Context, und anders als bei
einem Git-basierten Portainer-Stack wird es nicht automatisch dorthin
geklont. Auf den Host kopieren, unter einem Verzeichnis (`CONFIG_DIR`,
Default `/volume2/docker/portainer-updater`):

```
/volume2/docker/portainer-updater/
├── analysis-layer/       # aus diesem Repo: Dockerfile, requirements.txt, app/
└── stacks.yml            # kann zu Beginn einfach "stacks: []" sein - siehe unten
```

`data/wud` und `data/analysis` (SQLite-Speicher) werden beim ersten Start
automatisch angelegt - die musst du nicht vorher erstellen. `stacks.yml`
selbst muss aber schon vor dem ersten Deploy als *Datei* existieren (notfalls
leer, also nur `stacks: []`) - sonst legt Docker beim Bind-Mount stattdessen
ein Verzeichnis an dieser Stelle an, was den Container beim Start zum
Absturz bringt.

`stacks.yml` musst du nicht von Hand ausfuellen: sobald der Stack laeuft,
auf **"Stacks entdecken"** im Dashboard klicken. Das liest alle Stacks ueber
die Portainer-API aus, ordnet Container ueber ihr
`com.docker.compose.project`-Label den Stacks zu und traegt Containername,
Portainer-Stackname und Environment-ID automatisch ein - gemergt mit dem, was
schon in `stacks.yml` steht (bereits gesetzte `github_repo`/`notes` werden nie
ueberschrieben). Zusaetzlich wird versucht, `github_repo` automatisch aus dem
OCI-Label `org.opencontainers.image.source` eines Images zu erkennen, falls
das Image so ein Label setzt; wo das nicht klappt, wird die Zeile mit einem
`TODO`-Kommentar markiert. Das Ergebnis landet als
`data/analysis/stacks.discovered.yml` auf dem Host und wird direkt im
Dashboard angezeigt - pruefen, was du davon in die echte `stacks.yml` auf dem
Host uebernehmen willst, dann auf "stacks.yml im laufenden Container neu
laden" klicken (oder den Container neu starten), damit es geladen wird.

Container ohne `stacks.yml`-Eintrag tauchen im Dashboard mit dem Hinweis
"nicht in stacks.yml konfiguriert" auf, werden aber nicht automatisch
analysiert oder aktualisiert.

### 3. Umgebungsvariablen konfigurieren

`.env.example` in diesem Repo listet jede Variable mit Erklaerung auf - das
ist eine Referenz zum Ausfuellen, keine Datei, die du deployst (in Portainer
kommen die Werte direkt in den "Environment variables"-Dialog des Stacks,
siehe Schritt 4). Pflicht: `CONFIG_DIR` (falls abweichend vom Default oben),
`PORTAINER_URL`, `PORTAINER_API_KEY`, `ANTHROPIC_API_KEY`.

Optional `GITHUB_TOKEN` (ohne Token gilt GitHubs oeffentliches Rate-Limit von
60 Requests/Stunde - bei vielen Stacks ggf. eng; mit Token 5000/Stunde).
Als **Fine-grained** Personal Access Token anlegen (GitHub -> Settings ->
Developer settings -> Fine-grained tokens -> Generate new token), Repository
access auf **"Public Repositories (read-only)"** setzen und bei Permissions
nichts ankreuzen - dieser Zugriffstyp gibt automatisch Lesezugriff auf die
Releases aller oeffentlichen Repos, mehr braucht dieses Tool nicht. Das deckt
jeden `github_repo`-Eintrag in `stacks.yml` ab, der auf ein oeffentliches Repo
zeigt (z.B. immich). Fuer eigene PRIVATE Repos reicht das nicht - dafuer
braeuchte man einen separaten Token mit "Only select repositories" +
Permission "Contents: Read-only", was dieses Tool aktuell nicht unterstuetzt
(nur ein globaler `GITHUB_TOKEN`). Fuer eigene Repos bringt die
Changelog-Analyse ohnehin selten etwas (man weiss ja selbst, was man geaendert
hat) - dort `github_repo` einfach weglassen.

### 4. Stack in Portainer anlegen

Stacks -> Add stack -> Web editor -> Inhalt von `docker-compose.yml`
reinkopieren -> unter "Environment variables" die Variablen aus Schritt 3
setzen -> Stack deployen.

Dashboard: `http://<host>:8000` (Port ueber `DASHBOARD_PORT` anpassbar).
WUD-eigenes UI (optional, zur Kontrolle): `http://<host>:3939`.

## Ablauf im Alltag

1. WUD prueft alle 6h (konfigurierbar ueber `WUD_WATCHER_CRON`) alle
   Container. Bei einem erkannten Update ruft WUD den analysis-layer auf, der
   automatisch die Analyse anstoesst.
2. Zusaetzlich fragt der analysis-layer selbst stuendlich (konfigurierbar
   ueber `ANALYSIS_POLL_INTERVAL_MINUTES`) bei WUD nach - als Sicherheitsnetz,
   falls der Webhook mal nicht ankommt oder ein Update schon vor dem ersten
   Start des Dashboards da war.
3. Im Dashboard erscheint eine Zeile pro offenem Update mit Risiko-Badge
   (unbedenklich / kleine Aenderungen / groessere Aenderungen / breaking
   changes), Klartext-Zusammenfassung, und - falls relevant - dem
   Compose-Diff-Vorschlag.
4. Stacks anhaken, optional "Vorgeschlagene compose-Aenderung uebernehmen"
   aktivieren, auf "Ausgewaehlte aktualisieren" klicken. Der analysis-layer
   schreibt die (ggf. gepatchte) compose-Datei zurueck nach Portainer und
   redeployed den Stack mit frisch gepullten Images.
5. "Ignorieren" markiert ein Update als erledigt, ohne etwas zu aendern
   (z.B. wenn man es manuell schon gemacht hat).

## Bekannte Grenzen / bitte pruefen

- Die Portainer-API-Feldnamen (`StackFileContent`, `RepullImageAndRedeploy`,
  `X-API-Key`-Header) wurden direkt gegen den Quellcode des Tags `2.39.5`
  verifiziert (passend zur hier eingesetzten Business Edition LTS) - sollten
  also ohne Anpassung funktionieren. Bei einem spaeteren Upgrade auf eine
  neuere Portainer-Version im Zweifel `PORTAINER_URL/api/docs` (Swagger) der
  eigenen Instanz gegenpruefen.
- Auch Images ohne Versions-Tag (z.B. `:latest`, von WUD per Digest-Aenderung
  erkannt) werden analysiert: die aktuelle Version wird aus einem
  OCI-Versions-Label des Images gelesen, falls vorhanden
  (`org.opencontainers.image.version` o.ae.), sonst ueber das
  Erstellungsdatum des laufenden Images genaehert (naechstliegendes
  GitHub-Release davor). Die neue Version ist schlicht das aktuellste
  GitHub-Release des Repos. Da das eine Naeherung und kein exakter
  Tag-Abgleich ist, markiert das Dashboard diese Zeilen mit einem `*` und
  einem erklaerenden Hinweis (der auch an Claude mitgegeben wird, das die
  Unsicherheit in seiner Zusammenfassung erwaehnt). Nur wenn sich weder ein
  Label noch ein passendes Release finden laesst (z.B. Repo ohne Releases,
  oder Image ohne Label und ohne verwertbares Erstellungsdatum), bleibt es
  bei "keine Analyse moeglich".
- Der von Claude vorgeschlagene `compose_patch` ist ein Vorschlag, kein
  garantiert korrekter Patch. Vor "Aenderung uebernehmen" den Diff im
  Dashboard pruefen, besonders bei Stacks mit sensiblen Daten (Datenbanken).
- Der Anthropic-API-Key verursacht laufende Kosten (eine Analyse pro neu
  erkannter Versions-Kombination, nicht pro Poll - Ergebnisse werden
  zwischengespeichert).
