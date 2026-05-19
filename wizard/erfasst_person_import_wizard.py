from odoo import fields, models
from odoo.exceptions import UserError

from ..core.erfasst_client import ErfasstClient, ErfasstApiError


class ErfasstPersonImportWizard(models.TransientModel):
    _name = 'erfasst.person.import.wizard'
    _description = '123erfasst Personen importieren / Zuordnung aktualisieren'

    state = fields.Selection(
        selection=[
            ('select', 'Laden'),
            ('preview', 'Vorschau'),
            ('done', 'Fertig'),
        ],
        default='select',
        required=True,
    )
    line_ids = fields.One2many(
        comodel_name='erfasst.person.import.wizard.line',
        inverse_name='wizard_id',
        string='Personen',
    )
    imported_count = fields.Integer(string='Zugeordnet / Aktualisiert', readonly=True)

    def action_fetch(self):
        self.ensure_one()
        try:
            client = ErfasstClient.from_env(self.env)
            persons = client.get_persons()
        except ErfasstApiError as exc:
            raise UserError(f'123erfasst API-Fehler: {exc}') from exc

        # Bestehende Zuordnungen: {erfasst_person_ident: mapping_record}
        existing_mappings = {
            m.erfasst_person_ident: m
            for m in self.env['erfasst.employee.mapping'].sudo().search([])
        }

        self.line_ids.unlink()
        line_vals = []
        for p in persons:
            ident = p.get('ident') or ''
            if not ident:
                continue
            name = f"{p.get('firstname', '')} {p.get('lastname', '')}".strip()
            contact = p.get('contact') or {}
            email = contact.get('email') or ''
            mapping = existing_mappings.get(ident)
            line_vals.append({
                'wizard_id': self.id,
                'erfasst_ident': ident,
                'erfasst_name': name,
                'erfasst_email': email,
                'employee_id': mapping.employee_id.id if mapping else False,
                'status': 'mapped' if mapping else 'new',
            })

        self.env['erfasst.person.import.wizard.line'].create(line_vals)
        self.write({'state': 'preview'})
        return self._reopen()

    def action_confirm(self):
        self.ensure_one()
        Mapping = self.env['erfasst.employee.mapping'].sudo()
        existing = {
            m.erfasst_person_ident: m
            for m in Mapping.search([])
        }
        saved = 0
        for line in self.line_ids:
            if not line.employee_id:
                continue
            mapping = existing.get(line.erfasst_ident)
            vals = {
                'erfasst_person_ident': line.erfasst_ident,
                'erfasst_person_name': line.erfasst_name,
                'employee_id': line.employee_id.id,
            }
            if mapping:
                if (mapping.employee_id.id != line.employee_id.id
                        or mapping.erfasst_person_name != line.erfasst_name):
                    mapping.write({
                        'employee_id': line.employee_id.id,
                        'erfasst_person_name': line.erfasst_name,
                    })
                    saved += 1
            else:
                Mapping.create(vals)
                saved += 1

        self.write({'state': 'done', 'imported_count': saved})
        return self._reopen()

    def action_back(self):
        self.write({'state': 'select'})
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Personen aus 123erfasst',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class ErfasstPersonImportWizardLine(models.TransientModel):
    _name = 'erfasst.person.import.wizard.line'
    _description = '123erfasst Personen-Import Zeile'

    wizard_id = fields.Many2one(
        comodel_name='erfasst.person.import.wizard',
        required=True,
        ondelete='cascade',
    )
    erfasst_ident = fields.Char(string='Ident', readonly=True)
    erfasst_name = fields.Char(string='Name in 123erfasst', readonly=True)
    erfasst_email = fields.Char(string='E-Mail', readonly=True)
    employee_id = fields.Many2one(
        comodel_name='hr.employee',
        string='Odoo-Mitarbeiter',
    )
    status = fields.Selection(
        selection=[
            ('new', 'Neu'),
            ('mapped', 'Bereits zugeordnet'),
        ],
        string='Status',
        readonly=True,
    )
