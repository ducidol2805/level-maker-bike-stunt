# Log curved ramp và campaign_05

**Cập nhật 2026-09-23:** Đại ca xác nhận curved ramp đã chỉnh từ dữ liệu thật là
tiêu chuẩn. Các failure curved ramp bên dưới phản ánh điều kiện test cũ, không phải
lỗi library. Test đã đổi sang kiểm tra bảo toàn geometry override. `campaign_05`
đã được rebuild riêng từ library hiện tại. Nội dung bên dưới giữ làm log trước khi xử lý;
kết quả mới ở [tests_after_map05_rebuild.log](tests_after_map05_rebuild.log).

Ngày kiểm tra: 2026-09-23. Kiểm tra Python/static geometry, chưa kiểm chứng vật lý trong Unity.

Đã xóa recipe demo Cinder Crown, nhánh CLI tạo demo, test demo và tài liệu liên quan.
Đã bỏ `referenceLevel` trỏ tới demo khỏi recipe campaign và 30 map đã lưu.
Không có file map demo trên đĩa hoặc thành viên demo trong `levels/levels_final_01.zip`.
Không tái sinh campaign và không sửa geometry library/map trong lần kiểm tra này.

## Kết quả chạy test

```powershell
python -m unittest discover -s tests -v
```

41 test methods: 38 pass, 3 fail; unittest ghi nhận **7 failures, 0 errors** vì hai
test curved ramp có mỗi test 3 subtest thất bại. Lỗi demo không còn.

- 6 failures: 3 variant curved ramp × 2 điều kiện geometry.
- 1 failure: dữ liệu đã lưu của campaign_05 khác dữ liệu dựng từ library hiện tại.
- 5 smoke test hai app đều pass.

Traceback đầy đủ: [tests_after_demo_removal.log](tests_after_demo_removal.log).
Dữ liệu chẩn đoán đầy đủ: [geometry_differences.json](geometry_differences.json).

## Curved ramp: override không khớp điều kiện của test

Nguồn: `library/types/curved_ramp.json`, trường
`variants[*].geometryOverrides.MainPlatform.launch`.

`bike_stunt/library_variants.py` dựng geometry từ parameters trước, sau đó gọi
`apply_geometry_overrides()` trong `bike_stunt/obstacle_library.py` để thay các điểm.
Ba variant dưới đây có override làm thay đổi tangent; các test vẫn yêu cầu hình học
của builder gốc. Đây là xung đột giữa dữ liệu override và điều kiện test, chưa đủ
cơ sở kết luận hình học được chỉnh trong editor là sai ý đồ thiết kế.

### 1. Tangent vào điểm nén khác kỳ vọng

Test: `test_jump_family_geometry.JumpFamilyGeometryTests.test_curved_ramps_keep_original_compression_entry`
tại `tests/test_jump_family_geometry.py:112`.

Vị trí: `MainPlatform/launch/points[2].tangentIn`, index bắt đầu từ 0.
Tọa độ trong bảng là local space chưa nhân world scale.

| Variant | Kỳ vọng `(x, y)` | Thực tế `(x, y)` |
| --- | --- | --- |
| `curved_ramp.gentle_scoop` | `(-1.7, 0)` | `(-2.24, -0.14)` |
| `curved_ramp.deep_scoop` | `(-1.973333, 0)` | `(-1.44, 0.12)` |
| `curved_ramp.speed_scoop` | `(-2.52, 0)` | `(-2.2, -0.46)` |

### 2. Độ dốc đầu đoạn phóng khác 0

Test: `test_jump_family_geometry.JumpFamilyGeometryTests.test_launch_ascent_curves_up_without_a_shoulder`
tại `tests/test_jump_family_geometry.py:89`.

Test yêu cầu đoạn cuối của driving surface bắt đầu với tangent ngang.
Độ dốc được tính từ hai control point đầu của đoạn Bezier cuối, tương đương
`tangentOut.y / tangentOut.x` ở điểm áp chót.

| Variant | Kỳ vọng | Thực tế, làm tròn |
| --- | --- | --- |
| `curved_ramp.gentle_scoop` | `0` | `0.0625` |
| `curved_ramp.deep_scoop` | `0` | `-0.0833333333` |
| `curved_ramp.speed_scoop` | `0` | `0.2090909091` |

Mỗi subtest dừng ở assertion đầu tiên này; các assertion phía sau về độ dốc tăng dần
và góc ra chưa được chạy cho ba variant lỗi. Không suy diễn rằng chúng đã pass.

Đối chứng trong bộ nhớ: bỏ `geometryOverrides` khỏi bản sao của ba variant thì cả
`tangentIn` tại điểm nén và độ dốc đầu đoạn phóng khớp kỳ vọng.
`trick_scoop` và `raised_catch` không có các sai lệch trên.
Không ghi thay đổi đối chứng vào library.

## Campaign_05: 6 sai lệch cụ thể

Test: `test_world_scale.WorldScaleTests.test_saved_map_is_scaled_once_and_spring_arc_keeps_shape`
tại `tests/test_world_scale.py:46`.

Phép so sánh: bản đã lưu, bỏ `map.worldScale` và `design.validationReport`, so với
`scale_world_data(export_level(build_authored_level(recipe_05, families)), 2)`.
"Kỳ vọng" dưới đây là đầu ra builder với library hiện tại; "đã lưu" là file trên đĩa.

Các đường dẫn dùng JSON Pointer, index mảng bắt đầu từ 0.

| Đường dẫn | Đã lưu | Kỳ vọng |
| --- | --- | --- |
| `/FreePlatform` | Không có trường | `[]` |
| `/MainPlatform/5/points/5/tangentIn/x` | `-5.04` | `-4.4` |
| `/MainPlatform/5/points/5/tangentIn/y` | `-0.0` | `-0.92` |
| `/MainPlatform/5/points/5/tangentOut/x` | `0.7523231972285247` | `4.4` |
| `/MainPlatform/5/points/5/tangentOut/y` | `0` | `0.92` |
| `/MainPlatform/5/points/6/tangentMode` | `broken` | `linear` |

`MainPlatform[5].id` là `s12_spring_landing_recovery`: terrain đã merge nên tên shape
không mang tên curved ramp. Phần thay đổi thuộc instance `s14`, variant
`curved_ramp.speed_scoop` trong sequence của map.

Đối chứng trong bộ nhớ: bỏ các override curved ramp khi dựng bản kỳ vọng và thêm
`FreePlatform: []` vào bản sao dữ liệu đã lưu thì hai cấu trúc **bằng nhau hoàn toàn**.
Bằng chứng này xác định map lưu đang khớp geometry builder gốc, chưa đồng bộ các
override curved ramp hiện tại và trường rỗng `FreePlatform`.

Assertion so sánh toàn map thất bại trước các kiểm tra spring. Em đã chạy riêng
những kiểm tra còn lại để tránh gán nhầm lỗi cho world scale hoặc spring:

- `terrainExport.boundaryPadding == 40`: pass.
- `export_level(saved) == saved`: pass, export vẫn idempotent.
- Quỹ đạo spring sau scale 2×: sai số tọa độ lớn nhất `5.684341886080802e-14`,
  nằm trong sai số số thực và đạt độ chính xác mặc định của `assertAlmostEqual`.

Lần này chỉ ghi log. Chưa xóa override, sửa điều kiện geometry test hoặc tái sinh map.
