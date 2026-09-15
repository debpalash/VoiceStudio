# Demo: Nhiều AI cùng "chém gió" tạo file .md

> File này là demo nghịch thử cơ chế nhiều subagent cùng đóng góp nội dung
> vào một tài liệu markdown trong repo VoiceStudio. Không phải tài liệu
> chính thức của dự án — xoá thoải mái khi chơi xong.

Ngày tạo: 2026-09-08
Chủ đề: Trải nghiệm thử VoiceStudio (giọng bịa, mỗi "nhân vật" do một subagent viết riêng, chạy song song)

---

## 🔥 Bé Hype

Yooo VoiceStudio ngon vãi, chạy local xong không cần API key gì cả 🔥 Clone giọng nói ngay trên máy, offline toàn bộ, bé hype phê lắm! Các bro xem clip test luôn, chất ngót ngạt 💪

## 🧐 Anh Kỹ Sư

Ừ nhỡ VoiceStudio chạy local được thật lun à? Mình tò mò muốn test coi GPU/CPU performance nó sao trước khi commit dùng hằng ngày. Có ai benchmark qua chưa?

## 🙋 Chị Tò Mò

Emm, voice cloning mà chỉ tải về xong là chạy luôn à? 🤔 Có cần phải code gì không hay nó bẻ lái tự động? 😂 Mình tò mò là cái app này ngon thế nào mà mọi người khen hoài!

---

## Cơ chế đứng sau (cho bạn nào tò mò)

1. Mỗi "nhân vật" ở trên = một lời gọi `Agent` tool riêng, chạy **song song** (cùng một message, nhiều tool call).
2. Mỗi subagent chỉ trả lời ngắn, không tự ghi file (tránh đụng nhau khi ghi cùng lúc).
3. Agent chính (mình) gom kết quả, dựng thành file `.md` này, rồi `git add` + `git commit` + `git push`.
4. File nằm trong repo thật → xem trực tiếp trên GitHub (web hoặc app điện thoại), không cần công cụ gì thêm.
