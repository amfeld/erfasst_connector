from datetime import date, datetime, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..core.erfasst_client import ErfasstClient, ErfasstApiError


class ProjectProject(models.Model):
    _inherit = 'project.project'

    x_123erfasst_project_ident = fields.Char(
        string='123erfasst Ident',
        readonly=True,
        copy=False,
        help='Interne UUID des Projekts in 123erfasst (wird nach erstem Push gesetzt)',
    )
    x_123erfasst_synced_at = fields.Datetime(
        string='Letzter Projekt-Push',
        readonly=True,
        copy=False,
    )
    x_123erfasst_status = fields.Selection(
        selection=[
            ('not_synced', 'Nicht synchronisiert'),
            ('synced', 'Synchronisiert'),
            ('error', 'Fehler'),
        ],
        string='Sync-Status',
        default='not_synced',
        readonly=True,
        copy=False,
    )
    x_123erfasst_last_pull = fields.Datetime(
        string='Letzter Zeiten-Import',
        readonly=True,
        copy=False,
        help='Zeitstempel des letzten erfolgreichen Zeiterfassungs-Imports (für Delta-Sync)',
    )
    x_123erfasst_project_number = fields.Char(
        string='Auftragsnummer',
        copy=False,
        help='Auftragsnummer für 123erfasst (Pflichtfeld beim Push).',
    )

    # ------------------------------------------------------------------
    # Public actions (called from buttons in project form view)
    # ------------------------------------------------------------------

    def action_123erfasst_push_project(self):
        self.ensure_one()
        try:
            client = ErfasstClient.from_env(self.env)
            entity = client.upsert_project([self._build_project_input()])
        except ErfasstApiError as exc:
            self.write({'x_123erfasst_status': 'error'})
            raise UserError(f'123erfasst API-Fehler: {exc}') from exc

        self.write({
            'x_123erfasst_project_ident': entity.get('ident', ''),
            'x_123erfasst_synced_at': fields.Datetime.now(),
            'x_123erfasst_status': 'synced',
        })

        self.env['erfasst.sync.log'].create({
            'name': f'Projekt gepusht: {self.name}',
            'operation': 'push_project',
            'project_id': self.id,
            'state': 'ok',
            'records_processed': 1,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '123erfasst',
                'message': f'Projekt „{self.name}" erfolgreich synchronisiert.',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_123erfasst_pull_times(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Zeiten aus 123erfasst importieren',
            'res_model': 'erfasst.pull.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_project_id': self.id},
        }

    def action_123erfasst_push_planning(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planung zu 123erfasst übertragen',
            'res_model': 'erfasst.planning.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_project_id': self.id},
        }

    # ------------------------------------------------------------------
    # Cron: delta-sync all synced projects
    # ------------------------------------------------------------------

    @api.model
    def _cron_pull_all_projects(self):
        try:
            client = ErfasstClient.from_env(self.env)
        except UserError as exc:
            self.env['erfasst.sync.log'].create({
                'name': 'Auto-Pull: Konfiguration fehlt',
                'operation': 'pull_times',
                'state': 'error',
                'detail': str(exc),
            })
            return

        create_tasks = self.env['ir.config_parameter'].sudo().get_param(
            'erfasst_connector.create_tasks', 'False'
        ) == 'True'

        mapping_dict = self._build_mapping_dict()
        synced = self.search([('x_123erfasst_project_ident', '!=', False)])

        for project in synced:
            try:
                if project.x_123erfasst_last_pull:
                    date_from = project.x_123erfasst_last_pull - timedelta(hours=2)
                else:
                    date_from = datetime.now() - timedelta(days=30)
                date_to = datetime.now()

                times = client.get_times({
                    'project': {'ident': {'_eq': project.x_123erfasst_project_ident}},
                    'dateFrom': {'_gte': date_from.strftime('%Y-%m-%d')},
                    'dateTo': {'_lte': date_to.strftime('%Y-%m-%d')},
                })

                new_count, updated_count, skip_count = project._import_times(
                    times, mapping_dict, create_tasks=create_tasks
                )
                project.x_123erfasst_last_pull = date_to

                has_issues = skip_count > 0
                self.env['erfasst.sync.log'].create({
                    'name': f'Auto-Pull: {project.name}',
                    'operation': 'pull_times',
                    'project_id': project.id,
                    'state': 'warning' if has_issues else 'ok',
                    'records_processed': new_count + updated_count + skip_count,
                    'records_updated': updated_count,
                    'records_skipped': skip_count,
                    'detail': (
                        f'{new_count} neu, {updated_count} aktualisiert, '
                        f'{skip_count} übersprungen.'
                    ),
                })

            except ErfasstApiError as exc:
                self.env['erfasst.sync.log'].create({
                    'name': f'Auto-Pull FEHLER: {project.name}',
                    'operation': 'pull_times',
                    'project_id': project.id,
                    'state': 'error',
                    'detail': str(exc),
                })

    # ------------------------------------------------------------------
    # Shared import logic (used by cron and pull wizard)
    # ------------------------------------------------------------------

    def _import_times(
        self, times: list, mapping_dict: dict, create_tasks: bool = False
    ) -> tuple:
        """Create or update account.analytic.line records from 123erfasst StaffTime.

        Returns (new_count, updated_count, skipped_count).

        Deduplication key: x_123erfasst_time_ident.
        Gesperrte Einträge (x_123erfasst_locked=True) werden nie verändert —
        sie repräsentieren bereits abgegrenzte Kosten.
        """
        self.ensure_one()

        existing = {
            rec.x_123erfasst_time_ident: rec
            for rec in self.env['account.analytic.line'].sudo().search([
                ('x_123erfasst_time_ident', '!=', False),
                ('project_id', '=', self.id),
            ])
        }

        vals_list = []
        updated_count = skipped = 0

        for t in times:
            ident = t.get('ident') or ''
            if not ident:
                continue

            person_ident = (t.get('person') or {}).get('ident')
            emp_id = mapping_dict.get(person_ident)

            hours = self._compute_hours(t.get('timeStart'), t.get('timeEnd'))
            activity = t.get('activity') or {}
            activity_ident = activity.get('ident') or ''
            activity_name = activity.get('name') or ''
            description = t.get('text') or activity_name or '/'
            locked = bool(t.get('isLocked') or t.get('isProved'))

            if ident in existing:
                rec = existing[ident]
                # Gesperrte Einträge sind unveränderlich
                if rec.x_123erfasst_locked:
                    skipped += 1
                    continue
                if not emp_id:
                    skipped += 1
                    continue
                changes = {}
                if abs(rec.unit_amount - hours) > 0.001:
                    changes['unit_amount'] = round(hours, 4)
                if rec.name != description:
                    changes['name'] = description
                if rec.x_123erfasst_locked != locked:
                    changes['x_123erfasst_locked'] = locked
                if create_tasks and activity_ident:
                    new_task_id = self._get_or_create_task(activity_ident, activity_name)
                    if rec.task_id.id != new_task_id:
                        changes['task_id'] = new_task_id
                if changes:
                    # sudo() umgeht ACL; write() aus account_analytic_line propagiert Kostenstelle
                    rec.sudo().write(changes)
                    updated_count += 1
                else:
                    skipped += 1
                continue

            # Neu anlegen
            if not emp_id:
                skipped += 1
                continue

            task_id = False
            if create_tasks and activity_ident:
                task_id = self._get_or_create_task(activity_ident, activity_name)

            vals_list.append({
                'project_id': self.id,
                'employee_id': emp_id,
                'date': t.get('date'),
                'unit_amount': round(hours, 4),
                'name': description,
                'x_123erfasst_time_ident': ident,
                'x_123erfasst_locked': locked,
                'task_id': task_id or False,
            })

        if vals_list:
            self.env['account.analytic.line'].sudo().create(vals_list)

        return len(vals_list), updated_count, skipped

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _123erfasst_fid(self) -> str:
        self.ensure_one()
        return f'odoo-project-{self.id}'

    def _build_project_input(self) -> dict:
        self.ensure_one()
        partner = self.partner_id
        ICP = self.env['ir.config_parameter'].sudo()
        element_ident = ICP.get_param('erfasst_connector.element_ident', '').strip()

        project_number = self.x_123erfasst_project_number or str(self.id)
        start_date = (self.date_start.isoformat()
                      if getattr(self, 'date_start', None)
                      else date.today().isoformat())

        inp = {
            'fid': self._123erfasst_fid(),
            'id': project_number,
            'name': self.name,
            'description': self.description or None,
            'status': 'ACTIVE',
            'startDate': start_date,
            'street': partner.street or None,
            'zipCode': partner.zip or None,
            'city': partner.city or None,
        }

        if element_ident:
            inp['elementIdent'] = element_ident
            inp['costCenter'] = {
                'fid': f'odoo-costcenter-{self.id}',
                'id': project_number,
                'name': self.name,
                'elementIdent': element_ident,
                'startDate': start_date,
            }

        return inp

    @api.model
    def _build_mapping_dict(self) -> dict:
        """Return {erfasst_person_ident: employee_id} for all mappings."""
        mappings = self.env['erfasst.employee.mapping'].sudo().search([])
        return {m.erfasst_person_ident: m.employee_id.id for m in mappings}

    def _get_or_create_task(self, activity_ident: str, activity_name: str) -> int:
        """Sucht oder erstellt project.task per 123erfasst-Tätigkeits-Ident.

        Stabile Zuordnung per ident — Umbenennung in 123erfasst aktualisiert den Task-Namen.
        """
        self.ensure_one()
        Task = self.env['project.task'].sudo()
        task = Task.search([
            ('project_id', '=', self.id),
            ('x_123erfasst_activity_ident', '=', activity_ident),
        ], limit=1)
        if task:
            if task.name != activity_name:
                task.write({'name': activity_name})
        else:
            task = Task.create({
                'name': activity_name,
                'project_id': self.id,
                'x_123erfasst_activity_ident': activity_ident,
            })
        return task.id

    @staticmethod
    def _compute_hours(time_start, time_end) -> float:
        if not time_start or not time_end:
            return 0.0
        try:
            start = datetime.fromisoformat(time_start)
            end = datetime.fromisoformat(time_end)
            if end > start:
                return (end - start).total_seconds() / 3600.0
        except ValueError:
            pass
        return 0.0
