USE nirvana;

ALTER TABLE product_inventory
  ADD COLUMN unit_label VARCHAR(24) NULL,
  ADD COLUMN unit_price_paise BIGINT UNSIGNED NULL,
  ADD COLUMN unit_cost_paise BIGINT UNSIGNED NULL;

ALTER TABLE pickup_orders
  ADD COLUMN payment_status ENUM(
    'unpaid', 'initiating', 'pending', 'paid', 'failed', 'refund_due', 'refunding', 'refunded'
  )
    NOT NULL DEFAULT 'unpaid',
  ADD COLUMN total_amount_paise BIGINT UNSIGNED NULL,
  ADD COLUMN merchant_order_id VARCHAR(63) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ADD COLUMN phonepe_order_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ADD COLUMN merchant_refund_id VARCHAR(63) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ADD COLUMN phonepe_refund_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ADD COLUMN paid_at DATETIME NULL,
  ADD COLUMN refunded_at DATETIME NULL,
  ADD KEY ix_pickup_orders_payment_created (payment_status, created_at),
  ADD UNIQUE KEY uq_pickup_orders_merchant_order (merchant_order_id),
  ADD UNIQUE KEY uq_pickup_orders_merchant_refund (merchant_refund_id);

ALTER TABLE pickup_order_items
  ADD COLUMN unit_label VARCHAR(24) NULL,
  ADD COLUMN unit_price_paise BIGINT UNSIGNED NULL,
  ADD COLUMN unit_cost_paise BIGINT UNSIGNED NULL;
