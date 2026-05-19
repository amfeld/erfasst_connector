from odoo import fields, models


class ErfasstSyncLog(models.Model):
    _name = 'erfasst.sync.log'
    _description = '123erfasst Synchronisationsprotokoll'
    _order = 'create_date desc'
    _rec_name = 'name'

    name = fields.Char(string='Vorgang', required=True)
    operation = fields.Selection(
        selection=[
            ('push_project', 'Projekt → 123erfasst'),
            ('pull_projects', 'Projekte ← 123erfasst'),
            ('pull_times', 'Zeiten ← 123erfasst'),
            ('push_planning', 'Planung → 123erfasst'),
        ],
        string='Typ',
        required=True,
    )
    project_id = fields.Many2one(
        comodel_name='project.project',
        string='Projekt',
        ondelete='set null',
        index=True,
    )
    state = fields.Selection(
        selection=[
            ('ok', 'Erfolg'),
            ('warning', 'Warnung'),
            ('error', 'Fehler'),
        ],
        string='Status',
        required=True,
    )
    records_processed = fields.Integer(string='Verarbeitet', default=0)
    records_updated = fields.Integer(string='Aktualisiert', default=0)
    records_skipped = fields.Integer(string='Übersprungen', default=0)
    detail = fields.Text(string='Details')
    user_id = fields.Many2one(
        comodel_name='res.users',
        string='Benutzer',
        default=lambda self: self.env.user,
        readonly=True,
        ondelete='set null',
    )
