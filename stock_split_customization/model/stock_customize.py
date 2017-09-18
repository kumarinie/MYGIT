# -*- coding: utf-8 -*-
from openerp import models, fields, api
from openerp.exceptions import Warning
from openerp.tools.translate import _
import openerp.addons.decimal_precision as dp
from datetime import datetime

class stock_picking(models.Model):
    """Adds picking split without done state."""

    _inherit = "stock.picking"

    @api.multi
    def split_process(self):
        """Use to trigger the wizard from button with
           correct context"""
        ctx = {
            'active_model': self._name,
            'active_ids': self.ids,
            'active_id': len(self.ids) and self.ids[0] or False,
            'do_only_split': True,
            'default_picking_id': len(self.ids) and self.ids[0] or False,
        }
        picking_id, = self.ids
        picking = self.env['stock.picking'].browse(picking_id)
        view = self.env.ref('stock.view_stock_enter_transfer_details')
        if picking.picking_type_id.code == 'outgoing':
            view = self.env.ref('stock_split_customization.view_stock_enter_transfer_details_cus')
        return {
            'name': _('Enter quantities to split'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'stock.transfer_details',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': ctx,
        }

class stock_transfer_details_items(models.TransientModel):
    _inherit = 'stock.transfer_details_items'

    qty_original = fields.Float('Original Quantity', digits=dp.get_precision('Product Unit of Measure'),)
    qty_available = fields.Float('Quantity Available', digits=dp.get_precision('Product Unit of Measure'))

    @api.multi
    def quantity_onchange(self, product, quantity=0, qty_original=0, context=None):
        result = {}
        if product and quantity:
            prod = self.env['product.product'].browse(product)
            message = ''
            if quantity > prod.qty_available:
                message = "Requested quantity is unavailable"
            if quantity > qty_original:
                message = "You can not exceed the limit of requested quantity"
            if message:
                warning = {
                    'title': _('Warning!'),
                    'message': _(message),
                }
                result.update({'warning':warning})
                result.update({'value':{'quantity':min([qty_original,prod.qty_available])}})
        return result


class stock_transfer_details(models.TransientModel):
    _inherit = 'stock.transfer_details'

    def default_get(self, cr, uid, fields, context=None):
        if context is None: context = {}
        res = super(stock_transfer_details, self).default_get(cr, uid, fields, context=context)
        picking_ids = context.get('active_ids', [])
        active_model = context.get('active_model')

        if not picking_ids or len(picking_ids) != 1:
            # Partial Picking Processing may only be done for one picking at a time
            return res
        assert active_model in ('stock.picking'), 'Bad context propagation'
        picking_id, = picking_ids
        picking = self.pool.get('stock.picking').browse(cr, uid, picking_id, context=context)
        items = []
        packs = []
        if not picking.pack_operation_ids:
            picking.do_prepare_partial()
        for op in picking.pack_operation_ids:
            if picking.picking_type_id.code == 'outgoing':
                item = {
                    'packop_id': op.id,
                    'product_id': op.product_id.id,
                    'product_uom_id': op.product_uom_id.id,
                    'quantity': min([op.product_qty,op.product_id.qty_available]),
                    'qty_original': op.product_qty,
                    'qty_available': op.product_id.qty_available,
                    'package_id': op.package_id.id,
                    'lot_id': op.lot_id.id,
                    'sourceloc_id': op.location_id.id,
                    'destinationloc_id': op.location_dest_id.id,
                    'result_package_id': op.result_package_id.id,
                    'date': op.date,
                    'owner_id': op.owner_id.id,
                }
            else:
                item = {
                    'packop_id': op.id,
                    'product_id': op.product_id.id,
                    'product_uom_id': op.product_uom_id.id,
                    'quantity': op.product_qty,
                    'package_id': op.package_id.id,
                    'lot_id': op.lot_id.id,
                    'sourceloc_id': op.location_id.id,
                    'destinationloc_id': op.location_dest_id.id,
                    'result_package_id': op.result_package_id.id,
                    'date': op.date,
                    'owner_id': op.owner_id.id,
                }
            if op.product_id:
                items.append(item)
            elif op.package_id:
                packs.append(item)
        res.update(item_ids=items)
        res.update(packop_ids=packs)
        return res

    @api.one
    def do_detailed_transfer(self):
        if self.picking_id.state not in ['assigned', 'partially_available']:
            raise Warning(_('You cannot transfer a picking in state \'%s\'.') % self.picking_id.state)

        processed_ids = []
        # Create new and update existing pack operations
        for lstits in [self.item_ids, self.packop_ids]:
            for prod in lstits:
                if prod.quantity <= 0 and self.picking_id.picking_type_id.code == 'outgoing':
                    pack_id = self.env['stock.pack.operation'].search([('picking_id', '=', self.picking_id.id),('product_id', '=', prod.product_id.id)])
                    if pack_id:
                        query = "delete from stock_pack_operation where id = "+str(pack_id[0].id)
                        self._cr.execute(query)
                    move_id = self.env['stock.move'].search([('picking_id', '=', self.picking_id.id),('product_id', '=', prod.product_id.id)])
                    if move_id:
                        query = "delete from stock_move where id = "+str(move_id[0].id)
                        self._cr.execute(query)
                    continue
                else:
                    pack_datas = {
                        'product_id': prod.product_id.id,
                        'product_uom_id': prod.product_uom_id.id,
                        'product_qty': prod.quantity,
                        'package_id': prod.package_id.id,
                        'lot_id': prod.lot_id.id,
                        'location_id': prod.sourceloc_id.id,
                        'location_dest_id': prod.destinationloc_id.id,
                        'result_package_id': prod.result_package_id.id,
                        'date': prod.date if prod.date else datetime.now(),
                        'owner_id': prod.owner_id.id,
                    }
                    if prod.packop_id:
                        prod.packop_id.with_context(no_recompute=True).write(pack_datas)
                        processed_ids.append(prod.packop_id.id)
                    else:
                        pack_datas['picking_id'] = self.picking_id.id
                        packop_id = self.env['stock.pack.operation'].create(pack_datas)
                        processed_ids.append(packop_id.id)
        # Delete the others
        packops = self.env['stock.pack.operation'].search(['&', ('picking_id', '=', self.picking_id.id), '!', ('id', 'in', processed_ids)])
        packops.unlink()

        # Execute the transfer of the picking
        self.picking_id.do_transfer()

        return True

