from odoo import fields, models
from odoo.exceptions import UserError

from ..core.erfasst_client import ErfasstClient, ErfasstApiError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    erfasst_api_url = fields.Char(
        string='123erfasst API-URL',
        config_parameter='erfasst_connector.api_url',
        help='Vollständige GraphQL-Endpunkt-URL, z. B. https://firma.123erfasst.de/api/graphql',
    )
    erfasst_api_token = fields.Char(
        string='123erfasst API-Token',
        config_parameter='erfasst_connector.api_token',
        help='Basic-Auth-Token (Base64-kodierter Wert aus den 123erfasst API-Einstellungen)',
    )
    erfasst_element_ident = fields.Char(
        string='Element-Ident (Betrieb)',
        config_parameter='erfasst_connector.element_ident',
        help='UUID des 123erfasst-Elements (Betrieb/Niederlassung) — gilt für alle Projekte.',
    )
    erfasst_sync_updates = fields.Boolean(
        string='Geänderte Zeiten aktualisieren',
        config_parameter='erfasst_connector.sync_updates',
        help='Bereits importierte Zeiteinträge werden aktualisiert, wenn sie sich in 123erfasst '
             'geändert haben (Stunden, Beschreibung). Gesperrte Einträge werden nie verändert.',
    )
    erfasst_create_tasks = fields.Boolean(
        string='Aufgaben aus Tätigkeiten erstellen',
        config_parameter='erfasst_connector.create_tasks',
        help='Beim Zeitimport wird für jede 123erfasst-Tätigkeit automatisch eine Odoo-Aufgabe '
             'im Projekt gesucht oder angelegt und mit dem Zeiteintrag verknüpft.',
    )
    # Cron-Steuerung: liest/schreibt direkt den ir.cron-Record
    erfasst_cron_active = fields.Boolean(string='Delta-Sync automatisch ausführen')
    erfasst_cron_interval = fields.Integer(
        string='Intervall (Stunden)',
        help='Wie oft der automatische Delta-Sync ausgeführt wird.',
    )

    def get_values(self):
        res = super().get_values()
        cron = self.env.ref(
            'erfasst_connector.ir_cron_erfasst_pull', raise_if_not_found=False
        )
        if cron:
            res['erfasst_cron_active'] = cron.sudo().active
            res['erfasst_cron_interval'] = cron.sudo().interval_number
        else:
            res['erfasst_cron_active'] = False
            res['erfasst_cron_interval'] = 24
        return res

    def set_values(self):
        super().set_values()
        cron = self.env.ref(
            'erfasst_connector.ir_cron_erfasst_pull', raise_if_not_found=False
        )
        if cron:
            interval = max(1, self.erfasst_cron_interval or 24)
            cron.sudo().write({
                'active': self.erfasst_cron_active,
                'interval_number': interval,
                'interval_type': 'hours',
            })

    def action_test_erfasst_connection(self):
        self.ensure_one()
        url = self.erfasst_api_url
        token = self.erfasst_api_token
        if not url or not token:
            raise UserError('Bitte API-URL und API-Token eingeben und speichern.')
        try:
            count = ErfasstClient(url, token).test_connection()
        except ErfasstApiError as exc:
            raise UserError(f'Verbindung fehlgeschlagen: {exc}') from exc
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '123erfasst Verbindungstest',
                'message': f'Verbindung erfolgreich — {count} Personen gefunden.',
                'type': 'success',
                'sticky': False,
            },
        }
