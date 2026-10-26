# Tài Liệu Đặc Tả Nghiệp Vụ (PRD): Hệ Thống Quản Lý Đơn Hàng & Mã Giảm Giá (Order & Discount Tier Service)

## 1. Thông Tin Chung
- **Mã tài liệu**: PRD-ORD-2026-001
- **Phiên bản**: 2.1.0
- **Trạng thái**: Approved (Sẵn sàng phát triển)
- **Phân hệ**: Order Processing & Pricing Engine (`target_repo/app`)
- **Mục tiêu**: Nâng cấp bộ máy tính tiền đơn hàng (Order Pricing Engine) để hỗ trợ chương trình Khách hàng Thân thiết theo Hạng thành viên (`DiscountTier`), đồng thời xử lý triệt để các lỗi biên (edge cases) khi giỏ hàng rỗng hoặc chứa sản phẩm Flash Sale.

---

## 2. Bối Cảnh & Vấn Đề Hiện Tại
Hiện tại, hệ thống Order Service (`app/services.py`) chỉ mới hỗ trợ:
1. Tính tổng tiền hàng gốc (`subtotal`) từ danh sách các mặt hàng (`OrderItem`) trong đơn hàng.
2. Áp dụng mã giảm giá cố định (`voucher_discount`) và cộng phí vận chuyển (`shipping_fee`).
3. Tính giá trung bình mỗi đơn vị sản phẩm (`average_item_price`) trong báo cáo tóm tắt đơn hàng.

**Các vấn đề tồn đọng cần giải quyết:**
- **Thiếu cơ chế phân hạng chiết khấu (`DiscountTier`)**: Khách hàng thuộc các hạng thành viên (`SILVER`, `GOLD`, `VIP`) chưa được hưởng quyền lợi chiết khấu tự động trên đơn hàng.
- **Sai lệch nghiệp vụ với hàng Flash Sale**: Các mặt hàng đang trong chương trình Flash Sale (`is_flash_sale = True`) đã được giảm giá sâu ở mức giá gốc, nhưng chưa có quy tắc loại trừ khỏi chiết khấu hạng thành viên.
- **Lỗi nghiêm trọng (ZeroDivisionError)**: Khi giỏ hàng rỗng (`items = []`) hoặc tổng số lượng sản phẩm bằng `0`, hàm tính giá trung bình mỗi sản phẩm hoặc tổng kết đơn hàng bị lỗi chia cho 0 (`ZeroDivisionError`), gây sập tiến trình xử lý đơn hàng.

---

## 3. Đặc Tả Chi Tiết Nghiệp Vụ (Business Rules)

### 3.1. Phân Hạng Chiết Khấu Thành Viên (`DiscountTier`)
Hệ thống định nghĩa 4 mức hạng thành viên cho khách hàng:

| Hạng Thành Viên (`DiscountTier`) | Tỷ Lệ Giảm Giá (%) | Mức Giảm Tối Đa (Cap) | Điều Kiện Áp Dụng |
| :--- | :---: | :---: | :--- |
| `NONE` (Mặc định) | 0% | 0 VNĐ | Không áp dụng chiết khấu hạng thành viên. |
| `SILVER` | 5% | Không giới hạn | Áp dụng trên tổng giá trị các mặt hàng hợp lệ (không phải Flash Sale). |
| `GOLD` | 10% | Không giới hạn | Áp dụng trên tổng giá trị các mặt hàng hợp lệ (không phải Flash Sale). |
| `VIP` | 20% | **500,000 VNĐ** (`500_000`) | Áp dụng trên tổng giá trị các mặt hàng hợp lệ. Nếu 20% vượt quá 500,000 VNĐ thì chỉ giảm đúng 500,000 VNĐ. |

### 3.2. Quy Tắc Loại Trừ Hàng Flash Sale (`is_flash_sale`)
- Mỗi mặt hàng `OrderItem` có thuộc tính boolean `is_flash_sale` (mặc định là `False`).
- **Quy tắc cốt lõi**: Chiết khấu theo hạng thành viên (`DiscountTier`: `SILVER`, `GOLD`, `VIP`) **TUYỆT ĐỐI KHÔNG** áp dụng cho các mặt hàng có `is_flash_sale == True`.
- Công thức tính tổng tiền hợp lệ để tính chiết khấu hạng (`eligible_subtotal`):
  - `eligible_subtotal` = Tổng của (`unit_price * quantity`) cho tất cả `item` trong `order.items` có `item.is_flash_sale == False`.
  - `flash_sale_subtotal` = Tổng của (`unit_price * quantity`) cho tất cả `item` trong `order.items` có `item.is_flash_sale == True`.
  - `subtotal` = `eligible_subtotal + flash_sale_subtotal`.

### 3.3. Công Thức Tính Chiết Khấu Hạng (`tier_discount`)
Dựa trên `eligible_subtotal` và `discount_tier` của đơn hàng:
1. Nếu `discount_tier == NONE`: `tier_discount = 0.0`
2. Nếu `discount_tier == SILVER`: `tier_discount = eligible_subtotal * 0.05`
3. Nếu `discount_tier == GOLD`: `tier_discount = eligible_subtotal * 0.10`
4. Nếu `discount_tier == VIP`: `tier_discount = min(eligible_subtotal * 0.20, 500_000.0)`

*Lưu ý*: Nếu toàn bộ sản phẩm trong giỏ hàng đều là hàng Flash Sale (`eligible_subtotal == 0`), thì `tier_discount` của mọi hạng thành viên (kể cả `VIP`) đều phải bằng `0.0`.

### 3.4. Công Thức Tính Tổng Tiền Thanh Toán Cuối Cùng (`final_total`)
1. Tổng chiết khấu (`total_discount`) = `tier_discount + voucher_discount`.
2. Tiền hàng sau chiết khấu (`discounted_merchandise`) = `max(0.0, subtotal - total_discount)`.
   - Tổng mức giảm giá không được làm âm giá trị tiền hàng.
3. Phí vận chuyển (`shipping_fee`):
   - Nếu giỏ hàng rỗng (`len(items) == 0` hoặc tổng số lượng `total_quantity == 0`), phí vận chuyển tự động bằng `0.0` và `final_total = 0.0`.
   - Nếu đơn hàng có sản phẩm, cộng `shipping_fee` vào `discounted_merchandise`.
4. Công thức tổng quát khi đơn hàng có sản phẩm:
   - `final_total = max(0.0, subtotal - (tier_discount + voucher_discount)) + shipping_fee`

### 3.5. Xử Lý Giỏ Hàng Rỗng & Giá Trung Bình Sản Phẩm (`average_item_price`)
- Chỉ số `average_item_price` được tính bằng: `subtotal / total_quantity` (trong đó `total_quantity` là tổng `item.quantity` của toàn bộ mặt hàng trong giỏ).
- **Quy tắc an toàn**: Khi giỏ hàng rỗng (`items == []`) hoặc `total_quantity == 0`:
  - Không được phép ném lỗi `ZeroDivisionError`.
  - Giá trị `average_item_price` phải trả về `0.0`.
  - `subtotal = 0.0`, `tier_discount = 0.0`, `final_total = 0.0`.

---

## 4. Các Ca Kiểm Thử Nghiệp Vụ Chuẩn (Acceptance Criteria & Test Cases)

### Case 1: Khách hàng SILVER mua hàng thường và hàng Flash Sale
- Mặt hàng 1: Áo sơ mi (`unit_price = 400,000`, `quantity = 2`, `is_flash_sale = False`) -> `800,000`
- Mặt hàng 2: Giày chạy bộ (`unit_price = 1,000,000`, `quantity = 1`, `is_flash_sale = True`) -> `1,000,000`
- Hạng: `SILVER` (5%)
- Kết quả kỳ vọng:
  - `subtotal` = `1,800,000`
  - `eligible_subtotal` = `800,000`
  - `tier_discount` = `800,000 * 5% = 40,000`
  - Với `voucher_discount = 0`, `shipping_fee = 30,000` -> `final_total = 1,800,000 - 40,000 + 30,000 = 1,790,000`.

### Case 2: Khách hàng VIP chạm trần giảm giá 500,000 VNĐ
- Mặt hàng 1: Laptop (`unit_price = 3,500,000`, `quantity = 1`, `is_flash_sale = False`) -> `3,500,000`
- Hạng: `VIP` (20%, tối đa 500,000)
- Kết quả kỳ vọng:
  - 20% của `3,500,000` là `700,000` (> `500,000`).
  - `tier_discount` bị giới hạn ở mức `500,000`.
  - Với `voucher_discount = 0`, `shipping_fee = 0` -> `final_total = 3,000,000`.

### Case 3: Khách hàng VIP nhưng chưa chạm trần 500,000 VNĐ
- Mặt hàng 1: Tai nghe (`unit_price = 1,000,000`, `quantity = 1`, `is_flash_sale = False`)
- Hạng: `VIP` (20%, tối đa 500,000)
- Kết quả kỳ vọng:
  - `tier_discount` = `200,000`.

### Case 4: Giỏ hàng rỗng (`items = []`)
- Không phát sinh lỗi `ZeroDivisionError`.
- `subtotal = 0.0`, `tier_discount = 0.0`, `average_item_price = 0.0`, `final_total = 0.0`.

---

## 5. Yêu Cầu Kỹ Thuật (Technical Constraints)
- Sử dụng Python 3.11+ với `dataclasses` hoặc `pydantic` rõ ràng, có đầy đủ Type Hints.
- Giữ tính tương thích ngược (backward compatibility) với các hàm và trường dữ liệu đang được gọi bởi bộ test hiện hữu trong `tests/test_services.py`.
- Bổ sung đầy đủ Unit Tests bao phủ toàn bộ các nhánh nghiệp vụ (`NONE`, `SILVER`, `GOLD`, `VIP` dưới trần, `VIP` vượt trần, loại trừ `is_flash_sale`, và giỏ hàng rỗng).

