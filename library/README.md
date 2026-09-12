# Obstacle library v2

**16 type × 5 variant = 80 obstacle.** Parameter, difficulty và intent nằm trong từng file `types/`. Generator đọc index `obstacle_catalog.json` trực tiếp.

| Type | Năm variant |
| --- | --- |
| flat | launch run, checkpoint pad, braking shelf, rolling reset, finish release |
| rise | gentle, long, late steepening, two stage, crest relief |
| fall | gentle, long, late compression, terraced, steep shelf |
| hill | round roller, early crest, late crest, mesa, double crest |
| valley | shallow, deep, long floor, offset exit, double bowl |
| wave | two soft, three even, growing, irregular, trough train |
| kicker | low skip, quick pop, long flick, high pop, precision pop |
| curved_ramp | gentle scoop, deep scoop, speed scoop, trick scoop, raised catch |
| tabletop | learner, long deck, steep face, trick table, brake table |
| gap | intro, flow double, long clear, precision, trick gap |
| step_up | small shelf, long shelf, high shelf, precision shelf, uphill extension |
| step_down | gentle, long, deep, tight, trick |
| drop | small, deep, offset, brake, long runout |
| platform | flat bridge, arched bridge, uphill, downhill, wave |
| balance | single curb, double curb, unequal stairs, tall ledge, tight stairs |
| explosive_loop | round, wide, tall, late exit, wide high |

[Research và giới hạn](research.md).

## Xem thư viện

```powershell
python .\obstacle_library.py
python .\obstacle_library.py --output .\library\previews --atlas .\library\atlas
python .\level_visualizer.py .\library\previews
```

Preview là obstacle riêng, không phải map hoàn chỉnh nên không tự thêm 10 coin. Mỗi atlas có 5 hàng, một variant mỗi hàng; tỷ lệ X/Y bằng nhau. Viewer vẽ Bézier từ tangent.

## Ghép map

```powershell
python .\generate_campaign.py --output .\levels_v2 --count 50
python .\level_visualizer.py .\levels_v2
```

Generator chọn variant, dịch tọa độ local theo ports.entry/exit, gắn sourceVariant lên shape và tạo assembly ID riêng. Mỗi map có recovery, đúng 10 coin và checkpoint sau hard gap đủ xa End. Dùng --overwrite nếu chủ động thay output đã có. Các map cũ trong levels/ được giữ nguyên.

## Contract

Curve i→j có control points Pi, Pi+out_i, Pj+in_j, Pj. Tangent là offset theo point. Ground kín, ramp phụ mở; balance có ledge linear chủ ý. Hai port cùng vị trí và hướng là G1. Joint connector-loop có handle đối ứng bằng nhau nên đạt C1; collinear đơn thuần chưa đủ C1.

Speed window, camera, coin flight và loop speed là target ban đầu. Chưa có mô phỏng xe để chặn speed mismatch giữa obstacle; design.compositionStatus ghi giới hạn này. Xe không hỗ trợ bunny hop/air control phải loại variant liên quan trước khi phát hành.
