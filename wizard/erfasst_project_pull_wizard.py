from odoo import fields, models
from odoo.exceptions import UserError

from ..core.erfasst_client import ErfasstClient, ErfasstApiError


class ErfasstProjectPullWizard(models.TransientModel):
    _name = 'erfasst.project.pull.wizard'
    _description = '123erfasst Projekte importieren & verknüpfen'

    state = fields.Selection(
        [('select', 'Auswahl'), ('preview', 'Vorschau'), ('done', 'Fertig')],
        default='select',
        required=True,
    )
    preview_line_ids = fields.One2many(
        'erfasst.project.pull.wizard.line', 'wizard_id',
        string='Projekte',
    )
    linked_count = fields.Integer(string='Verknüpft', readonly=True)
    created_count = fields.Integer(string='Neu angelegt', readonly=True)

    def action_fetch_preview(self):
        self.ensure_one()
        try:
            client = ErfasstClient.from_env(self.env)
            projects = client.get_projects()
        except ErfasstApiError as exc:
            raise UserError(f'123erfasst API-Fehler: {exc}') from exc

        # Lookup 1: already linked by ident
        by_ident = {
            p.x_123erfasst_project_ident: p
            for p in self.env['project.project'].sudo().search([
                ('x_123erfasst_project_ident', '!=', False),
            ])
        }
        # Lookup 2: match by Auftragsnummer (only unlinked Odoo projects)
        linked_ids = {p.id for p in by_ident.values()}
        by_number = {
            p.x_123erfasst_project_number: p
            for p in self.env['project.project'].sudo().search([
                ('x_123erfasst_project_number', '!=', False),
                ('x_123erfasst_project_ident', '=', False),
            ])
        }

        lines = []
        for p in projects:
            ident = p.get('ident') or ''
            number = p.get('id') or ''

            if ident and ident in by_ident:
                existing = by_ident[ident]
                status = 'linked'
                selected = False          # bereits verknüpft, nichts zu tun
            elif number and number in by_number:
                existing = by_number[number]
                status = 'matched'
                selected = True           # automatisch per Auftragsnummer gefunden
            else:
                existing = False
                status = 'new'
                selected = False          # kein Match, Nutzer muss manuell zuordnen

            lines.append({
                'wizard_id': self.id,
                'erfasst_ident': ident,
                'erfasst_name': p.get('name') or '',
                'erfasst_project_number': number,
                'erfasst_api_status': p.get('status') or '',
                'project_id': existing.id if existing else False,
                'status': status,
                'selected': selected,
            })

        self.preview_line_ids.unlink()
        self.env['erfasst.project.pull.wizard.line'].create(lines)
        self.write({'state': 'preview'})
        return self._reopen()

    def action_confirm_import(self):
        self.ensure_one()
        now = fields.Datetime.now()
        linked = created = 0

        for line in self.preview_line_ids.filtered(lambda l: l.selected):
            if line.project_id:
                line.project_id.sudo().write({
                    'x_123erfasst_project_ident': line.erfasst_ident,
                    'x_123erfasst_project_number': line.erfasst_project_number,
                    'x_123erfasst_status': 'synced',
                    'x_123erfasst_synced_at': now,
                })
                linked += 1
            else:
                self.env['project.project'].sudo().create({
                    'name': line.erfasst_name,
                    'x_123erfasst_project_ident': line.erfasst_ident,
                    'x_123erfasst_project_number': line.erfasst_project_number,
                    'x_123erfasst_status': 'synced',
                    'x_123erfasst_synced_at': now,
                })
                created += 1

        self.env['erfasst.sync.log'].create({
            'name': 'Projekte aus 123erfasst importiert/verknüpft',
            'operation': 'pull_projects',
            'state': 'ok',
            'records_processed': linked + created,
            'records_updated': linked,
            'detail': f'{linked} verknüpft, {created} neu angelegt.',
        })

        self.write({
            'state': 'done',
            'linked_count': linked,
            'created_count': created,
        })
        return self._reopen()

    def action_back(self):
        self.ensure_one()
        self.write({'state': 'select'})
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }


class ErfasstProjectPullWizardLine(models.TransientModel):
    _name = 'erfasst.project.pull.wizard.line'
    _description = '123erfasst Projekte importieren – Zeile'

    wizard_id = fields.Many2one(
        'erfasst.project.pull.wizard', required=True, ondelete='cascade',
    )
    selected = fields.Boolean(string='Übernehmen', default=False)
    erfasst_ident = fields.Char(string='Ident', readonly=True)
    erfasst_name = fields.Char(string='Name (123erfasst)', readonly=True)
    erfasst_project_number = fields.Char(string='Auftragsnr.', readonly=True)
    erfasst_api_status = fields.Char(string='API-Status', readonly=True)
    project_id = fields.Many2one(
        'project.project',
        string='Odoo-Projekt',
        options="{'no_quick_create': True, 'no_create_edit': True}",
    )
    status = fields.Selection(
        [
            ('linked', 'Bereits verknüpft'),
            ('matched', 'Automatisch zugeordnet'),
            ('new', 'Kein Match'),
        ],
        string='Status',
        readonly=True,
    )
