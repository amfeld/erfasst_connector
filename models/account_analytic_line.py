from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    x_123erfasst_time_ident = fields.Char(
        string='123erfasst Ident',
        index=True,
        readonly=True,
        copy=False,
        help='Eindeutige ID des Zeiteintrags in 123erfasst (verhindert Doppel-Import)',
    )
    x_123erfasst_locked = fields.Boolean(
        string='In 123erfasst gesperrt',
        default=False,
        readonly=True,
        help='Gesetzt wenn der Zeiteintrag in 123erfasst gesperrt oder geprüft wurde',
    )

    def write(self, values):
        # Gesperrte 123erfasst-Einträge dürfen nicht verändert werden —
        # sie repräsentieren abgegrenzte Kosten (approved/invoiced in 123erfasst).
        cost_keys = {'unit_amount', 'date', 'employee_id', 'project_id', 'task_id'}
        if cost_keys.intersection(values):
            locked = self.filtered(
                lambda r: r.x_123erfasst_locked and r.x_123erfasst_time_ident
            )
            if locked:
                raise UserError(
                    _('%d gesperrte(r) 123erfasst-Zeiteintrag/-einträge können nicht '
                      'geändert werden. Bitte zuerst in 123erfasst entsperren.')
                    % len(locked)
                )
        return super().write(values)

    def unlink(self):
        locked = self.filtered(
            lambda r: r.x_123erfasst_locked and r.x_123erfasst_time_ident
        )
        if locked:
            raise UserError(
                _('%d gesperrte(r) 123erfasst-Zeiteintrag/-einträge können nicht '
                  'gelöscht werden. Bitte zuerst in 123erfasst entsperren.')
                % len(locked)
            )
        return super().unlink()
