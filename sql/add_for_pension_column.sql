-- הוספת עמודה for_pension לטבלת סוגי רכיבי תשלום
-- תוספות עם for_pension=true ייוצאו לסמל 379 (תוספות לפנסיה) במקום 371 (תוספות)
ALTER TABLE payment_component_types ADD COLUMN IF NOT EXISTS for_pension BOOLEAN NOT NULL DEFAULT FALSE;

-- הוספת סמל 379 לטבלת payment_codes
INSERT INTO payment_codes (internal_key, merav_code, display_name, display_order)
VALUES ('extras_for_pension', '379', 'תוספות לפנסיה', 71)
ON CONFLICT DO NOTHING;
