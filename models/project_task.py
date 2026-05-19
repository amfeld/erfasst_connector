from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    x_123erfasst_activity_ident = fields.Char(
        string='123erfasst Tätigkeits-Ident',
        index=True,
        readonly=True,
        copy=False,
        help='Stabiler Schlüssel der 123erfasst-Tätigkeit. Wird beim Zeitimport automatisch gesetzt.',
    )
