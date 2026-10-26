from dataclasses import dataclass, field


@dataclass
class OrderItem:
    """Represents a single line item in a customer order."""

    product_id: str
    name: str
    unit_price: float
    quantity: int
    is_flash_sale: bool = False

    def __post_init__(self) -> None:
        if self.unit_price < 0:
            raise ValueError("unit_price cannot be negative")
        if self.quantity < 0:
            raise ValueError("quantity cannot be negative")

    @property
    def line_total(self) -> float:
        """Calculate total price for this item line."""
        return self.unit_price * self.quantity


@dataclass
class Order:
    """Represents a customer order prior to checkout calculation."""

    order_id: str
    customer_id: str
    items: list[OrderItem] = field(default_factory=list)
    voucher_discount: float = 0.0
    shipping_fee: float = 30_000.0


@dataclass
class OrderSummary:
    """Represents the calculated financial summary of an order."""

    order_id: str
    subtotal: float
    total_quantity: int
    average_item_price: float
    discount_amount: float
    shipping_fee: float
    final_total: float

