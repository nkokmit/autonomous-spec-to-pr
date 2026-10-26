from app.models import Order, OrderItem
from app.services import OrderPricingService


def test_calculate_order_summary_standard_items() -> None:
    """Test standard order calculation without voucher discount."""
    service = OrderPricingService()
    order = Order(
        order_id="ORD-001",
        customer_id="CUST-101",
        items=[
            OrderItem(
                product_id="SKU-1",
                name="Mechanical Keyboard",
                unit_price=1_200_000.0,
                quantity=1,
            ),
            OrderItem(
                product_id="SKU-2",
                name="Gaming Mouse",
                unit_price=400_000.0,
                quantity=2,
            ),
        ],
        voucher_discount=0.0,
        shipping_fee=30_000.0,
    )

    summary = service.calculate_order_summary(order)

    assert summary.order_id == "ORD-001"
    assert summary.subtotal == 2_000_000.0
    assert summary.total_quantity == 3
    assert round(summary.average_item_price, 2) == 666_666.67
    assert summary.discount_amount == 0.0
    assert summary.shipping_fee == 30_000.0
    assert summary.final_total == 2_030_000.0


def test_calculate_order_summary_with_voucher_discount() -> None:
    """Test order calculation with a fixed voucher discount applied."""
    service = OrderPricingService()
    order = Order(
        order_id="ORD-002",
        customer_id="CUST-102",
        items=[
            OrderItem(
                product_id="SKU-3",
                name="USB-C Hub",
                unit_price=500_000.0,
                quantity=2,
            ),
        ],
        voucher_discount=150_000.0,
        shipping_fee=25_000.0,
    )

    summary = service.calculate_order_summary(order)

    assert summary.order_id == "ORD-002"
    assert summary.subtotal == 1_000_000.0
    assert summary.total_quantity == 2
    assert summary.average_item_price == 500_000.0
    assert summary.discount_amount == 150_000.0
    assert summary.shipping_fee == 25_000.0
    assert summary.final_total == 875_000.0

