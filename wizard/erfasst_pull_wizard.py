from datetime import date, timedelta

from odoo import fields, models
from odoo.exceptions import UserError

from ..core.erfasst_client import ErfasstClient, ErfasstApiError


class ErfasstPullWizard(models.TransientModel):
    _name = 'erfasst.pull.wizard'
    _description = '123erfasst Zeiten importieren'

    state = fields.Selection(
        selection=[
            ('select', 'Parameter'),
            ('preview', 'Vorschau'),
            ('done', 'Fertig'),
        ],
        default='select',
        required=True,
    )
    project_id = fields.Many2one(
        comodel_name='project.project',
        string='Projekt',
        required=True,
        readonly=True,
    )
    date_from = fields.Date(
        string='Von',
        required=True,
        default=lambda self: self._default_date_from(),
    )
    date_to = fields.Date(
        string='Bis',
        required=True,
        default=lambda self: date.today(),
    )
    preview_line_ids = fields.One2many(
        comodel_name='erfasst.pull.wizard.line',
        inverse_name='wizard_id',
        string='Vorschau',
        readonly=True,
    )
    preview_total = fields.Integer(string='Gesamt', readonly=True)
    preview_new = fields.Integer(string='Neu', readonly=True)
    preview_changed = fields.Integer(string='Geändert', readonly=True)
    preview_skipped = fields.Integer(string='Übersprungen', readonly=True)
    imported_count = fields.Integer(string='Neu importiert', readonly=True)
    updated_count = fields.Integer(string='Aktualisiert', readonly=True)
    log_id = fields.Many2one(
        comodel_name='erfasst.sync.log',
        string='Protokolleintrag',
        readonly=True,
    )

    def _default_date_from(self):
        project_id = self.env.context.get('default_project_id')
        if project_id:
            project = self.env['project.project'].browse(project_id)
            if project.x_123erfasst_last_pull:
                return project.x_123erfasst_last_pull.date()
        return date.today() - timedelta(days=7)

    # ------------------------------------------------------------------
    # Wizard actions
    # ------------------------------------------------------------------

    def action_fetch_preview(self):
        self.ensure_one()
        project = self.project_id

        if not project.x_123erfasst_project_ident:
            raise UserError(
                'Dieses Projekt ist noch nicht mit 123erfasst verknüpft.\n'
                'Bitte zuerst „Zu 123erfasst pushen" oder „Projekte aus 123erfasst importieren" verwenden.'
            )

        try:
            client = ErfasstClient.from_env(self.env)
            times = client.get_times({
                'project': {'ident': {'_eq': project.x_123erfasst_project_ident}},
                'dateFrom': {'_gte': self.date_from.isoformat()},
                'dateTo': {'_lte': self.date_to.isoformat()},
            })
        except ErfasstApiError as exc:
            raise UserError(f'123erfasst API-Fehler: {exc}') from exc

        mapping_dict = self.env['project.project']._build_mapping_dict()

        # Dict ident→record für Update-Vergleich
        existing = {
            rec.x_123erfasst_time_ident: rec
            for rec in self.env['account.analytic.line'].sudo().search([
                ('x_123erfasst_time_ident', '!=', False),
                ('project_id', '=', project.id),
            ])
        }

        line_vals = []
        for t in times:
            ident = t.get('ident') or ''
            person = t.get('person') or {}
            person_ident = person.get('ident') or ''
            person_name = f"{person.get('firstname', '')} {person.get('lastname', '')}".strip()
            activity = t.get('activity') or {}
            activity_name = activity.get('name') or ''
            hours = self.env['project.project']._compute_hours(
                t.get('timeStart'), t.get('timeEnd')
            )
            description = t.get('text') or activity_name or '/'
            locked = bool(t.get('isLocked') or t.get('isProved'))

            emp_id = mapping_dict.get(person_ident) if person_ident else False

            if ident in existing:
                rec = existing[ident]
                if rec.x_123erfasst_locked:
                    status = 'duplicate'   # gesperrt → unveränderlich, zeige als duplicate
                elif not emp_id:
                    status = 'no_employee'
                else:
                    # Prüfe ob sich Werte geändert haben
                    diff = (
                        abs(rec.unit_amount - hours) > 0.001
                        or rec.name != description
                        or rec.x_123erfasst_locked != locked
                    )
                    status = 'changed' if diff else 'duplicate'
            elif not emp_id:
                status = 'no_employee'
            else:
                status = 'new'

            line_vals.append({
                'wizard_id': self.id,
                'erfasst_ident': ident,
                'date': t.get('date'),
                'hours': hours,
                'employee_id': emp_id or False,
                'erfasst_person_name': person_name,
                'activity_ident': activity.get('ident') or '',
                'activity_name': activity_name,
                'description': description,
                'locked': locked,
                'status': status,
                'selected': status in ('new', 'changed'),
            })

        self.preview_line_ids.unlink()
        self.env['erfasst.pull.wizard.line'].create(line_vals)

        new_count = sum(1 for v in line_vals if v['status'] == 'new')
        changed_count = sum(1 for v in line_vals if v['status'] == 'changed')
        skipped = sum(1 for v in line_vals if v['status'] not in ('new', 'changed'))

        self.write({
            'state': 'preview',
            'preview_total': len(line_vals),
            'preview_new': new_count,
            'preview_changed': changed_count,
            'preview_skipped': skipped,
        })
        return self._reopen()

    def action_confirm_import(self):
        self.ensure_one()
        project = self.project_id
        mapping_dict = self.env['project.project']._build_mapping_dict()

        # Neuerstellungen
        new_lines = self.preview_line_ids.filtered(lambda l: l.status == 'new' and l.selected)
        vals_list = []
        for line in new_lines:
            task_id = False
            if line.activity_ident:
                task_id = project._get_or_create_task(line.activity_ident, line.activity_name)
            vals_list.append({
                'project_id': project.id,
                'employee_id': line.employee_id.id,
                'date': line.date,
                'unit_amount': round(line.hours, 4),
                'name': line.description or line.activity_name or '/',
                'x_123erfasst_time_ident': line.erfasst_ident,
                'x_123erfasst_locked': line.locked,
                'task_id': task_id or False,
            })
        if vals_list:
            self.env['account.analytic.line'].sudo().create(vals_list)

        # Updates für geänderte Einträge
        changed_lines = self.preview_line_ids.filtered(lambda l: l.status == 'changed' and l.selected)
        updated = 0
        if changed_lines:
            existing = {
                rec.x_123erfasst_time_ident: rec
                for rec in self.env['account.analytic.line'].sudo().search([
                    ('x_123erfasst_time_ident', 'in', changed_lines.mapped('erfasst_ident')),
                    ('project_id', '=', project.id),
                ])
            }
            for line in changed_lines:
                rec = existing.get(line.erfasst_ident)
                if not rec or rec.x_123erfasst_locked:
                    continue
                changes = {}
                if abs(rec.unit_amount - line.hours) > 0.001:
                    changes['unit_amount'] = round(line.hours, 4)
                if rec.name != (line.description or line.activity_name or '/'):
                    changes['name'] = line.description or line.activity_name or '/'
                if rec.x_123erfasst_locked != line.locked:
                    changes['x_123erfasst_locked'] = line.locked
                if line.activity_ident:
                    new_task_id = project._get_or_create_task(line.activity_ident, line.activity_name)
                    if rec.task_id.id != new_task_id:
                        changes['task_id'] = new_task_id
                if changes:
                    rec.sudo().write(changes)
                    updated += 1

        project.x_123erfasst_last_pull = fields.Datetime.now()

        skipped_count = self.preview_skipped
        log = self.env['erfasst.sync.log'].create({
            'name': f'Zeiten importiert: {project.name}',
            'operation': 'pull_times',
            'project_id': project.id,
            'state': 'ok' if skipped_count == 0 else 'warning',
            'records_processed': len(vals_list) + updated + skipped_count,
            'records_updated': updated,
            'records_skipped': skipped_count,
            'detail': (
                f'{len(vals_list)} neu erstellt, {updated} aktualisiert, '
                f'{skipped_count} übersprungen.'
            ),
        })

        self.write({
            'state': 'done',
            'imported_count': len(vals_list),
            'updated_count': updated,
            'log_id': log.id,
        })
        return self._reopen()

    def action_back(self):
        self.write({'state': 'select'})
        return self._reopen()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Zeiten aus 123erfasst importieren',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class ErfasstPullWizardLine(models.TransientModel):
    _name = 'erfasst.pull.wizard.line'
    _description = '123erfasst Zeiten-Import Vorschau-Zeile'

    wizard_id = fields.Many2one(
        comodel_name='erfasst.pull.wizard',
        required=True,
        ondelete='cascade',
    )
    erfasst_ident = fields.Char(string='Ident', readonly=True)
    date = fields.Date(string='Datum', readonly=True)
    hours = fields.Float(string='Stunden', readonly=True, digits=(6, 2))
    employee_id = fields.Many2one(
        comodel_name='hr.employee',
        string='Mitarbeiter',
        readonly=True,
    )
    erfasst_person_name = fields.Char(string='Name in 123erfasst', readonly=True)
    activity_ident = fields.Char(string='Tätigkeits-Ident', readonly=True)
    activity_name = fields.Char(string='Tätigkeit', readonly=True)
    description = fields.Char(string='Beschreibung', readonly=True)
    locked = fields.Boolean(string='Gesperrt', readonly=True)
    selected = fields.Boolean(string='Import', default=True)
    status = fields.Selection(
        selection=[
            ('new', 'Neu'),
            ('changed', 'Geändert (wird aktualisiert)'),
            ('duplicate', 'Bereits importiert'),
            ('no_employee', 'Mitarbeiter nicht zugeordnet'),
        ],
        string='Status',
        readonly=True,
    )
