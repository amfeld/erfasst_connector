from odoo import fields, models


class ErfasstEmployeeMapping(models.Model):
    _name = 'erfasst.employee.mapping'
    _description = '123erfasst Mitarbeiter-Zuordnung'
    _rec_name = 'employee_id'

    employee_id = fields.Many2one(
        comodel_name='hr.employee',
        string='Odoo-Mitarbeiter',
        required=True,
        ondelete='cascade',
        index=True,
    )
    erfasst_person_ident = fields.Char(
        string='Person-Ident (UUID)',
        required=True,
        index=True,
        help='UUID des Mitarbeiters in 123erfasst (aus den API-Daten oder der 123erfasst-Oberfläche)',
    )
    erfasst_person_name = fields.Char(
        string='Name in 123erfasst',
        readonly=True,
        help='Zuletzt aus 123erfasst gelesener Anzeigename (informativ)',
    )

    _sql_constraints = [
        (
            'unique_employee',
            'UNIQUE(employee_id)',
            'Für jeden Odoo-Mitarbeiter ist nur eine 123erfasst-Zuordnung erlaubt.',
        ),
        (
            'unique_ident',
            'UNIQUE(erfasst_person_ident)',
            'Jeder 123erfasst-Person-Ident darf nur einmal zugeordnet sein.',
        ),
    ]
