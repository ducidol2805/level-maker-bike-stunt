# Cơ sở thiết kế obstacle — 12/09/2026

## Nguồn tham khảo

1. [Lee Rowland, Senior Level Designer, RedLynx — phỏng vấn Trials Evolution](https://www.gamegrin.com/articles/interview-with-lee-rowland-senior-level-designer-at-redlynx/).
   Rowland mô tả vai trò của restart nhanh, checkpoint như mục tiêu nhìn thấy được và độ khó nhất quán trong track. Designer phải hoàn thành được obstacle; tester kiểm tra cả track không lỗi. Áp dụng: lưu difficulty, dành pad ổn định và tách geometry test khỏi playtest.

2. [Professor FatShady / University of Trials — training Trials Rising](https://www.redbull.com/au-en/trials-university-trials-rising-ubisoft-interview).
   Đoạn trả lời được lập chỉ mục nhấn mạnh bunny hop, uphill landing và throttle control. Áp dụng: mỗi type có primarySkill, mỗi variant có intent. Trang đầy đủ không trả nội dung khi mở lần này; không suy ra thông số hình học hay tốc độ từ nguồn này.

3. [Ubisoft — Trials Fusion Track Jam 2, thể lệ 2014, trang 1–2](https://static2.cdn.ubi.com/Trials/Trials-Fusion-Track-Jam-Official-Rules_BH.pdf).
   Tài liệu cuộc thi ưu tiên chất lượng xây dựng và độ thú vị, đề xuất thời gian hoàn thành 30–40 giây, Beginner–Hard. Đây là mục tiêu riêng của cuộc thi, không phải chuẩn áp dụng mọi map. Thư viện không ép chiều dài bằng con số này.

## Quyết định của project

Các quy tắc dưới đây là suy luận thiết kế từ skill và nguồn tham khảo, không phải thông số chính thức của Trials.

- 16 type, mỗi type 5 variant chọn thông số riêng: thay nhịp, tỷ lệ lên/xuống, độ cao/chiều dài landing, khoảng hở hoặc động tác.
- Gap rộng có catch dài có thể dễ hơn gap ngắn với catch hẹp. Difficulty 1–5 là nhãn nội bộ, chưa tương đương rating Trials.
- Teach → practice → combine trong một band kỹ năng; không tăng difficulty đột ngột ở cuối map.
- Gap, kicker, curved ramp, step-up/down và drop chứa approach/launch/flight/landing/recovery. Drop có launchLength=0, góc 0.
- Balance gồm ledge có góc cứng chủ ý, cần front-wheel lift/bunny hop. Phải loại nhóm này nếu xe không hỗ trợ.
- Explosive loop là cơ chế custom theo ảnh người dùng, không phải pattern do các nguồn xác nhận. Connector chung joint với loop, ground là fallback, checkpoint reset cần phục hồi connector.
- Coin flight là dự đoán chất điểm; đúng 10 coin mỗi map, filler lấy theo mặt đường thực.
- Checkpoint trên pad sau landing gap khó, xa End theo quy ước project. Restart từ đứng yên vẫn cần thử với xe.
- Tất cả variant hiện chưa được kiểm chứng bằng vehicle physics. Test curve/schema/quota không chứng minh chơi được.

## Điều kiện duyệt

Đo wheelbase, wheel radius, gravity, motor torque, brake và suspension. Thử từng module ở 80/90/100/110/120% target; ghi điểm đáp, va chạm, overshoot/undershoot, hướng xe và exit speed. Thử restart checkpoint từ vận tốc 0 và chạy cả map trước khi chuyển candidate sang approved.
