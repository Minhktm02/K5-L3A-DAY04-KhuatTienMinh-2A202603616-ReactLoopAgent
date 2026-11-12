# Lab 4 — Agent loop, evaluation và phân tích

## 1. Thiết kế Agent Loop

`run_agent(question, provider, tools, max_steps=6)` trong `agent.py` dùng trực tiếp provider adapter và tool registry có sẵn:

- Khởi tạo system/user messages; dùng `tools.system_prompt` và `tools.schemas` khi tools cung cấp, nếu không thì dùng giá trị mặc định.
- Mỗi lần gọi model là một step. Assistant message được append nguyên vẹn; tất cả tool calls trong response được xử lý trước lần gọi model tiếp theo.
- Arguments được parse thành JSON object. JSON sai được trả lại model dưới dạng `INVALID_ARGUMENTS`; exception từ tool và tool result sai kiểu cũng trở thành tool observation để model có cơ hội phản hồi.
- Mỗi tool result được gửi với đúng `tool_call_id`. Trace ghi model, tool, lỗi và source validation; usage được lưu theo từng model call.
- Chỉ `finish == 'stop'` cùng nội dung answer dạng string mới hoàn tất. `length`, lý do dừng khác, response sai cấu trúc và provider error không bị báo `completed`. Hết `max_steps` trả `max_steps`.

### Source validation

`AGENT_SOURCE_VALIDATION` mặc định là `1`; giá trị `0` chỉ tắt validation, còn giá trị khác `0`/`1` bị từ chối. Khi bật, Agent lưu URL không rỗng từ kết quả `get_incident_details` thành công. Nếu final answer thiếu một URL đã quan sát, Agent thêm event `validation_missing_source`, yêu cầu model đưa URL vào câu trả lời và tiếp tục trong cùng `max_steps`. Không bắt URL khi incident lookup thất bại. Đây chỉ xác minh URL xuất hiện, không chứng minh mọi claim được URL hỗ trợ.

## 2. Case phân tích: E2

**Câu hỏi:** Cache purge của Cloudflare có incident liên quan không, và website ngoài FedRAMP có chắc bị ảnh hưởng không?

Trace trong `eval_after.json` cho thấy:

```text
step 1: get_status(provider=cloudflare, component=cache purge)
step 2: get_incident_details(wp49bp8bqo6c), status=identified
        tool result có URL https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c
step 3: model finish=stop; validation_missing_source ghi URL trên
step 4–5: lấy chi tiết hai incident cache purge đã resolved
step 6: model finish=stop
```

Final answer nêu incident đang diễn ra “Fedramp Customers have Delayed Purges”, xác định phạm vi theo thông báo Cloudflare rằng khách hàng non-FedRAMP không bị ảnh hưởng, đồng thời liệt kê hai incident lịch sử đã resolved và các URL nguồn. Judge chấm **98/100**; reason đánh giá câu trả lời chính xác, nhưng improvement đề nghị nói rõ kết luận đó không loại trừ nguyên nhân khác nếu website thực tế gặp lỗi. Trace cho thấy validation được kích hoạt do câu trả lời ban đầu thiếu URL nguồn quan sát được; việc sửa không thay đổi tool registry hay quy tắc chấm.

## 3. Evaluation

Hai artifact hiện tại là một phép ablation: cùng provider/model Agent và Judge, cùng core dataset, `max_steps=8`, `workers=1`, và cùng grading; chỉ khác `AGENT_SOURCE_VALIDATION`. Lượt Before chạy với giá trị `0`, lượt After hiện có chạy với giá trị `1`. `eval_before.json` không ghi cờ môi trường vào trường `config`, nên giá trị Before được xác định từ cấu hình tiến trình lúc chạy. Lượt cũ bị HTTP 429 được giữ riêng trong `eval_before_http429_backup_20261112_1117.json` và không dùng trong phép so sánh này.

### Cấu hình và điểm tổng

| Thiết lập | Before | After |
|---|---|---|
| Agent provider/model | Gemini / `gemini-3.8-flash` | Gemini / `gemini-3.8-flash` |
| Judge provider/model | Gemini / `gemini-3.8-flash` | Gemini / `gemini-3.8-flash` |
| `AGENT_SOURCE_VALIDATION` | `0` | `1` |
| Dataset / max steps / workers | core / 8 / 1 | core / 8 / 1 |
| Kết quả | 22/22 scored, 0 unscored | 22/22 scored, 0 unscored |
| Điểm tổng | **99.55/100** | **99.59/100** |

Chênh lệch tổng quan Before→After là **+0.04 điểm**. Không có API provider errors. Cả hai lượt có 22 Agent runs `completed`; trace ghi 55 model calls ở Before và 61 ở After, trong đó After có 4 event `validation_missing_source`. Cả hai lượt có cùng 5 tool timeout observations giả lập trong dataset (E6 hai lần, A3, H7, H8); đây không phải API timeout.

Usage do provider ghi trong artifact, cộng Agent và Judge: Before có 91,569 prompt, 14,935 completion, 127,156 total tokens; After có 109,521 prompt, 17,532 completion, 148,773 total tokens. Đây là số liệu usage được provider báo, không phải chi phí.

### So sánh từng case

| Case | Before | After | Δ After−Before |
|---|---:|---:|---:|
| E1 | 100 | 100 | 0 |
| E2 | 100 | 98 | -2 |
| E3 | 100 | 100 | 0 |
| E4 | 100 | 100 | 0 |
| E5 | 100 | 100 | 0 |
| E6 | 100 | 100 | 0 |
| A1 | 100 | 100 | 0 |
| A2 | 95 | 98 | +3 |
| A3 | 100 | 100 | 0 |
| A4 | 100 | 100 | 0 |
| A5 | 100 | 100 | 0 |
| A6 | 100 | 100 | 0 |
| A7 | 100 | 100 | 0 |
| A8 | 100 | 100 | 0 |
| H1 | 95 | 95 | 0 |
| H2 | 100 | 100 | 0 |
| H3 | 100 | 100 | 0 |
| H4 | 100 | 100 | 0 |
| H5 | 100 | 100 | 0 |
| H6 | 100 | 100 | 0 |
| H7 | 100 | 100 | 0 |
| H8 | 100 | 100 | 0 |

Hai case có điểm khác nhau:

- **E2: 100→98 (-2).** Before không có event source-validation. After trace ghi `validation_missing_source` ở step 3 với URL incident `wp49bp8bqo6c`; lượt tiếp theo đưa URL vào answer. Judge vẫn cho 98 và nhận xét câu trả lời chính xác nhưng đề nghị thận trọng hơn khi suy rộng về nguyên nhân lỗi của website. Đây là quan sát cùng lượt với validation, không chứng minh validation làm giảm điểm.
- **A2: 95→98 (+3).** After answer nhận 98, nhưng trace không có `validation_missing_source`; do đó mức tăng này không phải bằng chứng rằng bước yêu cầu sửa nguồn đã cải thiện case. Judge ở hai lượt đưa ra nhận xét khác nhau về câu trả lời.
- 20 case còn lại giữ nguyên điểm. Case E2 giảm 2 và A2 tăng 3 nên chênh lệch tổng là +0.04 sau khi tổng hợp trên 22 case.

### Diễn giải và giới hạn

- Đây là so sánh cùng cấu hình danh nghĩa, chỉ bật/tắt cờ Source Validation; khác biệt model, judge, dataset hoặc max steps của baseline 2/22 cũ không còn áp dụng cho hai artifact hiện tại.
- Kết quả là một lượt cho mỗi chế độ. Agent và LLM judge có thể sinh kết quả khác nhau giữa các lượt; vì vậy chênh lệch điểm là kết quả quan sát được, không phải bằng chứng nhân quả ổn định về tác động của validation.
- Source validation kiểm tra URL từ incident lookup thành công có xuất hiện verbatim trong final answer; không kiểm chứng tính đúng đắn của mọi claim. Tác động trực tiếp thể hiện trong trace ở bốn event After.
- Evaluation là trên core dataset/snapshot của bài, không phải kiểm tra trạng thái dịch vụ trực tiếp ngoài dữ liệu đó.
- API key là credential cấu hình cục bộ, không phải thay đổi Agent. `.env` không thuộc bộ file nộp.

## 4. Kiểm thử và nộp bài

- `python -m unittest discover -s tests -v`: **37 tests PASS**, gồm multiple tool calls/IDs, invalid JSON, tool errors, `max_steps`, truncated output, source validation và failed-tool behavior.
- Hai artifact JSON được parse thành công, mỗi file có đúng 22 case và 22/22 scored; Before=99.55/100, After=99.59/100.
- `.env` không được track và bị Git ignore. Không tìm thấy API key đã cấu hình trong hai artifact hoặc NOTES.

### File nộp

Theo mục Nộp bài trong GUIDE, bộ file chính xác gồm `agent.py`, `eval_before.json`, `eval_after.json` và `NOTES.md`. Không nộp `.env`.

**Khuyến nghị không đưa `providers.py` vào bộ nộp riêng:** adapter đã được cung cấp sẵn và không cần sửa theo GUIDE; thay đổi diagnostics chỉ hỗ trợ điều tra HTTP 429, không phải cải tiến Agent. Không xóa hay revert thay đổi đó trong bước rà soát này. Nếu nộp nguyên repository, diff hiện có của `providers.py` vẫn nằm trong working tree; nên hỏi giảng viên hoặc khai báo rõ thay đổi này thay vì mô tả nó như yêu cầu của Lab 4.
