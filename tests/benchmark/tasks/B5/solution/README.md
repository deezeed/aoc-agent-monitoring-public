# shop

A tiny shop library: `shop.cart.cart_total(items)`.

Run the tests: `python -m unittest discover -s tests -t .`

## Coupons

`shop.coupons.apply_coupon(total, code)` (codes are case-insensitive):

- `SAVE10` -- 10 % off
- `WINTER25` -- 25 % off
- `FIVE` -- 5.00 EUR off, never below 0
