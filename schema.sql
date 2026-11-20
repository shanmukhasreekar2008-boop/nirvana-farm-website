CREATE DATABASE IF NOT EXISTS nirvana
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE nirvana;

CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  display_name VARCHAR(80) NOT NULL,
  email VARCHAR(254) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('customer', 'admin') NOT NULL DEFAULT 'customer',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS web_sessions (
  token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  csrf_token VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  user_id BIGINT UNSIGNED NULL,
  expires_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (token_hash),
  KEY ix_web_sessions_expiry (expires_at),
  CONSTRAINT fk_web_sessions_user
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS product_inventory (
  slug VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  name VARCHAR(100) NOT NULL,
  on_hand INT UNSIGNED NOT NULL DEFAULT 0,
  reserved_quantity INT UNSIGNED NOT NULL DEFAULT 0,
  unit_label VARCHAR(24) NULL,
  unit_price_paise BIGINT UNSIGNED NULL,
  unit_cost_paise BIGINT UNSIGNED NULL,
  display_order SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (slug),
  CONSTRAINT chk_inventory_reserved CHECK (reserved_quantity <= on_hand)
) ENGINE=InnoDB;

INSERT IGNORE INTO product_inventory (slug, name, on_hand, display_order) VALUES
  ('garden-tomatoes', 'Garden tomatoes', 0, 1),
  ('leafy-greens', 'Leafy greens', 0, 2),
  ('root-vegetables', 'Root vegetables', 0, 3);

CREATE TABLE IF NOT EXISTS pickup_orders (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NULL,
  customer_name VARCHAR(80) NOT NULL,
  customer_email VARCHAR(254) NOT NULL,
  note VARCHAR(1000) NOT NULL DEFAULT '',
  status ENUM('pending', 'confirmed', 'collected', 'cancelled') NOT NULL DEFAULT 'pending',
  payment_status ENUM(
    'unpaid', 'initiating', 'pending', 'paid', 'failed', 'refund_due', 'refunding', 'refunded'
  )
    NOT NULL DEFAULT 'unpaid',
  total_amount_paise BIGINT UNSIGNED NULL,
  merchant_order_id VARCHAR(63) CHARACTER SET ascii COLLATE ascii_bin NULL,
  phonepe_order_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  merchant_refund_id VARCHAR(63) CHARACTER SET ascii COLLATE ascii_bin NULL,
  phonepe_refund_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  paid_at DATETIME NULL,
  refunded_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_pickup_orders_status_created (status, created_at),
  KEY ix_pickup_orders_payment_created (payment_status, created_at),
  KEY ix_pickup_orders_user (user_id),
  UNIQUE KEY uq_pickup_orders_merchant_order (merchant_order_id),
  UNIQUE KEY uq_pickup_orders_merchant_refund (merchant_refund_id),
  CONSTRAINT fk_pickup_orders_user
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS pickup_order_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_id BIGINT UNSIGNED NOT NULL,
  product_slug VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  product_name VARCHAR(100) NOT NULL,
  quantity SMALLINT UNSIGNED NOT NULL,
  unit_label VARCHAR(24) NULL,
  unit_price_paise BIGINT UNSIGNED NULL,
  unit_cost_paise BIGINT UNSIGNED NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_order_product (order_id, product_slug),
  CONSTRAINT chk_order_item_quantity CHECK (quantity BETWEEN 1 AND 99),
  CONSTRAINT fk_order_items_order
    FOREIGN KEY (order_id) REFERENCES pickup_orders (id) ON DELETE CASCADE,
  CONSTRAINT fk_order_items_product
    FOREIGN KEY (product_slug) REFERENCES product_inventory (slug)
) ENGINE=InnoDB;
