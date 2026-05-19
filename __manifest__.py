{
    'name': 'Erfasst Connector – 123erfasst Zeiterfassung',
    'version': '17.0.1.0.0',
    'category': 'Project',
    'summary': 'Synchronisation zwischen Odoo-Projekten und 123erfasst (Bauzeiterfassung)',
    'author': 'Intern',
    'license': 'LGPL-3',
    'depends': [
        'hr_timesheet',   # Beinhaltet: project, hr, account, analytic
        'base_setup',     # res.config.settings
    ],
    'data': [
        'security/erfasst_security.xml',
        'security/ir.model.access.csv',
        'data/scheduled_actions.xml',
        'views/res_config_settings_views.xml',
        'views/erfasst_employee_mapping_views.xml',
        'views/erfasst_sync_log_views.xml',
        'views/erfasst_pull_wizard_views.xml',
        'views/erfasst_person_import_wizard_views.xml',
        'views/erfasst_planning_wizard_views.xml',
        'views/erfasst_project_pull_wizard_views.xml',
        'views/project_project_views.xml',
        'views/hr_employee_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,

    # ------------------------------------------------------------------
    # Beschreibung (wird im App-Store / Modul-Detail angezeigt)
    # ------------------------------------------------------------------
    'description': """
Erfasst Connector — 123erfasst Zeiterfassung
============================================

Dieses Modul verbindet Odoo 17 mit der Bauzeiterfassungs-SaaS **123erfasst**
über deren GraphQL-API. Es bleibt vollständig im Standard-Odoo-Datenmodell
(``project.project``, ``hr.timesheet``, ``planning.slot``) und benötigt
keine zusätzlichen Python-Abhängigkeiten (verwendet nur stdlib ``urllib``).

Funktionen
----------

**1. Projekte pushen**
    Überträgt ein Odoo-Projekt per Knopfdruck nach 123erfasst.
    Idempotent: ein erneuter Push aktualisiert den bestehenden Eintrag
    (via ``referBy: FID`` und ``fid = odoo-project-{id}``).
    Adressdaten werden aus dem verknüpften Partner übernommen.

**2. Zeiten importieren (manuell + automatischer Delta-Sync)**
    Holt StaffTime-Einträge aus 123erfasst und schreibt sie als
    ``account.analytic.line`` (hr.timesheet) in Odoo.

    * **Wizard**: Zeitraum wählen → Vorschau (neu / bereits importiert /
      kein Mitarbeiter) → Import bestätigen.
    * **Cron-Job** (täglich, standardmäßig deaktiviert): Delta-Sync
      pro Projekt – nur Einträge ab ``letzter Import − 2 Stunden`` werden
      abgerufen. Ein fehlerhafte Projekt unterbricht die anderen nicht.
    * **Deduplizierung**: Über das Feld ``x_123erfasst_time_ident``
      (indiziertes Char-Feld auf ``account.analytic.line``) — kein
      Doppelimport, auch bei überlappenden Zeitfenstern.

**3. Planung pushen**
    Überträgt ``planning.slot``-Einträge des Projekts (Odoo Planning-App)
    nach 123erfasst. Idempotent via ``fid = odoo-slot-{id}``.
    Mitarbeiter ohne 123erfasst-Zuordnung werden in der Vorschau
    markiert und übersprungen.

Einrichtung (Schritt für Schritt)
----------------------------------

**Schritt 1 – API-Zugangsdaten konfigurieren**
    Einstellungen → Allgemein → Abschnitt „123erfasst Connector"

    * **API-URL**: ``https://<kunde>.123erfasst.de/api/graphql``
    * **API-Token**: Base64-kodierter Basic-Auth-String des API-Nutzers
      (z. B. ``base64("nutzer:passwort")``)

**Schritt 2 – Mitarbeiter-Zuordnung anlegen**
    Mitarbeiter → Konfiguration → 123erfasst-Zuordnung
    (Berechtigung: Gruppe „123erfasst Manager" erforderlich)

    Jeder Odoo-Mitarbeiter, der Zeiten in 123erfasst bucht, muss hier
    mit seiner 123erfasst **Person-UUID** (``erfasst_person_ident``)
    verknüpft werden. Die UUID findet sich in der 123erfasst-API über
    die ``persons``-Query oder im Personen-Stamm der Anwendung.

**Schritt 3 – Projekt synchronisieren**
    Projekt öffnen → Schaltfläche **„Zu 123erfasst pushen"**

    Nach dem ersten Push werden gesetzt:
    * ``x_123erfasst_fid`` – externe FID (``odoo-project-{id}``)
    * ``x_123erfasst_project_ident`` – interne UUID in 123erfasst
    * ``x_123erfasst_status`` → „Synchronisiert"
    * Tab „123erfasst" im Projekt zeigt alle Sync-Felder

**Schritt 4a – Zeiten manuell importieren**
    Projekt öffnen → **„Zeiten importieren"** (nur nach erstem Push sichtbar)

    Wizard: Zeitraum → Vorschau prüfen → Importieren

**Schritt 4b – Automatischen Delta-Sync aktivieren**
    Einstellungen → Technisch → Geplante Aktionen →
    „123erfasst: Zeiten delta-synchronisieren" → aktivieren und
    gewünschte Uhrzeit einstellen (empfohlen: täglich 02:00 Uhr).

**Schritt 5 – Planung übertragen**
    Projekt öffnen → **„Planung pushen"** (nur nach erstem Push sichtbar)

    Wizard: Zeitraum wählen → Vorschau (welche Slots übertragen werden) →
    Planungseinträge pushen

Sicherheitsgruppen
------------------

* **123erfasst Benutzer** (``group_erfasst_user``):
  Alle internen Benutzer. Darf Projekte pushen, Zeiten importieren,
  Planung pushen und das Synchronisationsprotokoll lesen.

* **123erfasst Manager** (``group_erfasst_manager``):
  Zusätzlich: Mitarbeiter-Zuordnung verwalten (CRUD).

Protokoll / Audit-Log
----------------------
    Projektmenü → 123erfasst → Synchronisationsprotokoll

    Jede Push- und Pull-Operation erzeugt einen Eintrag mit Zeitstempel,
    Typ, Anzahl verarbeiteter / übersprungener Datensätze und Details.
    Filterbar nach Typ und Status.

Technische Hinweise
-------------------

* Keine externen Python-Pakete – nur stdlib ``urllib.request`` + ``json``.
* HTTP-Timeout: 30 Sekunden pro API-Request.
* Paginierung: automatisch in 200er-Blöcken (konfigurierbar in
  ``ErfasstClient._paginate``).
* FID-Schema: ``odoo-project-{project.id}`` und ``odoo-slot-{slot.id}`` –
  deterministisch, überlebt Re-Push ohne Duplikate.
* Zeiten-Deduplizierung: indiziertes Feld ``x_123erfasst_time_ident``
  auf ``account.analytic.line``. Das Feld wird beim Kopieren eines
  Timesheets nicht mitgenommen (``copy=False``).
* Delta-Sync-Puffer: 2 Stunden Überlapp bei jedem Cron-Lauf, um
  nachträglich bearbeitete Einträge zu erfassen. Doppelimporte
  werden durch die Deduplizierung verhindert.
""",
}
