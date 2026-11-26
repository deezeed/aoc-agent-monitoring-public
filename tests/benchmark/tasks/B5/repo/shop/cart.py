"""A tiny cart: prices in euros."""

PRICES = {"tea": 4.20, "mug": 9.90, "honey": 6.50}


def cart_total(items):
    """items = {product: quantity} -> total in euros, rounded to cents."""
    return round(sum(PRICES[name] * qty for name, qty in items.items()), 2)
