from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    x_123erfasst_mapping_ids = fields.One2many(
        comodel_name='erfasst.employee.mapping',
        inverse_name='employee_id',
        string='123erfasst-Zuordnung',
    )
    x_123erfasst_person_ident = fields.Char(
        compute='_compute_123erfasst_ident',
        string='123erfasst-Ident',
        store=False,
    )

    @api.depends('x_123erfasst_mapping_ids')
    def _compute_123erfasst_ident(self):
        for rec in self:
            mapping = rec.x_123erfasst_mapping_ids[:1]
            rec.x_123erfasst_person_ident = mapping.erfasst_person_ident if mapping else ''
