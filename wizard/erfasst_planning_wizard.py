from datetime import date, timedelta

from odoo import fields, models
from odoo.exceptions import UserError

from ..core.erfasst_client import ErfasstClient, ErfasstApiError


class ErfasstPlanningWizard(models.TransientModel):
    _name = 'erfasst.planning.wizard'
    _description = '123erfasst Planung übertragen'

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
    date_start = fields.Date(
        string='Von',
        required=True,
        default=lambda self: date.today(),
    )
    date_end = fields.Date(
        string='Bis',
        required=True,
        default=lambda self: date.today() + timedelta(days=7),
    )
    line_ids = fields.One2many(
        comodel_name='erfasst.planning.wizard.line',
        inverse_name='wizard_id',
        string='Planungseinträge',
        readonly=True,
    )
    pushed_count = fields.Integer(string='Übertragen', readonly=True)
    skipped_count = fields.Integer(string='Übersprungen', readonly=True)
    log_id = fields.Many2one(
        comodel_name='erfasst.sync.log',
        string='Protokolleintrag',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Wizard actions
    # ------------------------------------------------------------------

    def _check_planning_available(self):
        """Raise a clear UserError when the Enterprise 'planning' module is not installed."""
        if 'planning.slot' not in self.env:
            raise UserError(
                'Die Odoo Planning-App (Modul „planning") ist nicht installiert.\n'
                'Diese Funktion setzt Odoo Enterprise oder eine separate '
                'Installation des planning-Moduls voraus.'
            )

    def action_fetch_preview(self):
        self.ensure_one()
        self._check_planning_available()
        project = self.project_id

        if not project.x_123erfasst_project_ident:
            raise UserError(
                'Dieses Projekt wurde noch nicht zu 123erfasst synchronisiert.\n'
                'Bitte zuerst „Zu 123erfasst pushen" verwenden.'
            )

        # date_end inclusive: search slots that START before midnight of (date_end + 1)
        date_end_exclusive = self.date_end + timedelta(days=1)

        slots = self.env['planning.slot'].search([
            ('project_id', '=', project.id),
            ('start_datetime', '>=', fields.Datetime.to_datetime(self.date_start)),
            ('start_datetime', '<', fields.Datetime.to_datetime(date_end_exclusive)),
            ('employee_id', '!=', False),
        ])

        mapping = self.env['erfasst.employee.mapping'].search([])
        mapping_by_emp = {m.employee_id.id: m for m in mapping}

        line_vals = []
        for slot in slots:
            m = mapping_by_emp.get(slot.employee_id.id)
            status = 'ok' if m else 'no_employee'
            person_name = m.erfasst_person_name if m else ''
            minutes = 0
            if slot.start_datetime and slot.end_datetime:
                delta = slot.end_datetime - slot.start_datetime
                minutes = max(0, int(delta.total_seconds() / 60))

            line_vals.append({
                'wizard_id': self.id,
                'slot_id': slot.id,
                'employee_id': slot.employee_id.id,
                'erfasst_person_name': person_name,
                'date_start': slot.start_datetime,
                'date_end': slot.end_datetime,
                'minutes': minutes,
                'status': status,
            })

        self.line_ids.unlink()
        self.env['erfasst.planning.wizard.line'].create(line_vals)

        ok_count = sum(1 for v in line_vals if v['status'] == 'ok')
        skipped = len(line_vals) - ok_count

        self.write({
            'state': 'preview',
            'pushed_count': ok_count,
            'skipped_count': skipped,
        })
        return self._reopen()

    def action_confirm_push(self):
        self.ensure_one()
        project = self.project_id

        mapping = self.env['erfasst.employee.mapping'].search([])
        mapping_by_emp = {m.employee_id.id: m.erfasst_person_ident for m in mapping}

        ok_lines = self.line_ids.filtered(lambda l: l.status == 'ok')

        inputs = []
        for line in ok_lines:
            person_ident = mapping_by_emp.get(line.employee_id.id)
            if not person_ident:
                continue
            date_start = line.date_start.date() if line.date_start else None
            date_end = line.date_end.date() if line.date_end else None
            inputs.append({
                'fid': f'odoo-slot-{line.slot_id}',
                'projectIdent': project.x_123erfasst_project_ident,
                'personIdents': [person_ident],
                'dateStart': date_start.isoformat() if date_start else None,
                'dateEnd': date_end.isoformat() if date_end else None,
                'minutes': line.minutes,
            })

        if not inputs:
            raise UserError('Keine gültigen Planungseinträge zum Übertragen vorhanden.')

        try:
            client = ErfasstClient.from_env(self.env)
            client.upsert_planning(inputs)
        except ErfasstApiError as exc:
            raise UserError(f'123erfasst API-Fehler: {exc}') from exc

        log = self.env['erfasst.sync.log'].create({
            'name': f'Planung übertragen: {project.name}',
            'operation': 'push_planning',
            'project_id': project.id,
            'state': 'ok' if self.skipped_count == 0 else 'warning',
            'records_processed': len(inputs),
            'records_skipped': self.skipped_count,
            'detail': f'{len(inputs)} Planungseinträge übertragen.',
        })

        self.write({
            'state': 'done',
            'pushed_count': len(inputs),
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
            'name': 'Planung zu 123erfasst übertragen',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class ErfasstPlanningWizardLine(models.TransientModel):
    _name = 'erfasst.planning.wizard.line'
    _description = '123erfasst Planungs-Vorschau-Zeile'
    _order = 'date_start'

    wizard_id = fields.Many2one(
        comodel_name='erfasst.planning.wizard',
        required=True,
        ondelete='cascade',
    )
    slot_id = fields.Integer(string='Slot-ID', readonly=True)
    employee_id = fields.Many2one(
        comodel_name='hr.employee',
        string='Mitarbeiter',
        readonly=True,
    )
    erfasst_person_name = fields.Char(string='Name in 123erfasst', readonly=True)
    date_start = fields.Datetime(string='Start', readonly=True)
    date_end = fields.Datetime(string='Ende', readonly=True)
    minutes = fields.Integer(string='Minuten', readonly=True)
    status = fields.Selection(
        selection=[
            ('ok', 'Wird übertragen'),
            ('no_employee', 'Mitarbeiter nicht zugeordnet'),
        ],
        string='Status',
        readonly=True,
    )
