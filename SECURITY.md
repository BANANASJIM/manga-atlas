# Security

**English** | [简体中文](#安全)

MangaAtlas is meant to run on your own computer, for you alone. It has no authentication.

- It listens on `127.0.0.1` and rejects requests whose `Host` header is not its own, which blocks
  DNS rebinding.
- Requests other than `GET` and `HEAD` need a custom header that other web pages cannot send,
  and other sites may only link to its pages, not embed or fetch them.
- It stores no credentials.

Do not expose it to a network, for example through `host = "0.0.0.0"` or a reverse proxy.

MangaAtlas talks to Suwayomi without logging in, so Suwayomi's login must stay off. That leaves a
risk MangaAtlas cannot remove: Suwayomi's API answers cross-origin requests from any site, so a web
page open in your browser can read and change your Suwayomi library. Binding Suwayomi to
`127.0.0.1` keeps other devices out, but not pages in your own browser.

## Reporting a vulnerability

Please report it privately through GitHub: the repository's **Security** tab →
**Report a vulnerability**. Do not open a public issue. You will get an answer within a week.

---

## 安全

**[English](#security)** | 简体中文

MangaAtlas 设计为在你自己的电脑上、只给你一个人用，没有任何登录认证。

- 只监听 `127.0.0.1`，并拒绝 `Host` 头不是它自己的请求，以防 DNS 重绑定。
- 除 `GET`、`HEAD` 以外的请求必须带一个其他网页发不出的自定义请求头；其他网站只能链接到它的页面，不能嵌入或抓取。
- 不保存任何凭据。

不要把它暴露到网络上，比如设 `host = "0.0.0.0"` 或套反向代理。

MangaAtlas 不登录就访问 Suwayomi，所以 Suwayomi 的登录必须保持关闭。这留下一个 MangaAtlas 无法消除的风险：Suwayomi 的接口接受任何网站的跨域请求，你浏览器里打开的网页可以读取和修改你的 Suwayomi 书架。把 Suwayomi 只绑定在 `127.0.0.1` 能挡住其他设备，挡不住你自己浏览器里的网页。

### 报告漏洞

请通过 GitHub 私下报告：仓库的 **Security** 页 → **Report a vulnerability**。请不要开公开 issue。
一周内会回复。
