from app.models import Order, OrderSummary


class OrderPricingService:
    """Service responsible for calculating order totals and pricing breakdowns."""

    def calculate_subtotal(self, order: Order) -> float:
        """Calculate raw merchandise subtotal across all order items."""
        return float(sum(item.line_total for item in order.items))

    def calculate_total_quantity(self, order: Order) -> int:
        """Calculate total unit quantity across all order items."""
        return sum(item.quantity for item in order.items)

    def calculate_average_item_price(self, order: Order) -> float:
        """
        Calculate average price per unit across the order.
        NOTE: Contains a latent ZeroDivisionError bug when order.items is empty!
        """
        subtotal = self.calculate_subtotal(order)
        total_quantity = self.calculate_total_quantity(order)
        return subtotal / total_quantity

    def calculate_order_summary(self, order: Order) -> OrderSummary:
        """
        Calculate complete order pricing summary.
        Currently only applies flat voucher_discount and shipping_fee.
        Does NOT implement DiscountTier rules yet, and fails on empty cart due to
        calculate_average_item_price dividing by zero.
        """
        subtotal = self.calculate_subtotal(order)
        total_quantity = self.calculate_total_quantity(order)
        average_item_price = self.calculate_average_item_price(order)

        discount_amount = min(subtotal, max(0.0, order.voucher_discount))
        discounted_merchandise = max(0.0, subtotal - discount_amount)
        final_total = discounted_merchandise + order.shipping_fee

        return OrderSummary(
            order_id=order.order_id,
            subtotal=subtotal,
            total_quantity=total_quantity,
            average_item_price=average_item_price,
            discount_amount=discount_amount,
            shipping_fee=order.shipping_fee,
            final_total=final_total,
        )

