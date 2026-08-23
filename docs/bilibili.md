# Bilibili 接口模块

## 本次修复

`iter_submissions()` 不使用接口返回的虚假 `page.count`，只以短页、空页或明确
的分页失败作为终止信号；每条记录的 `mid` 若与请求 UID 不一致会被丢弃。页级
风控失败通过 `SubmissionPageError.page` 暴露给扫描器。

## 关键接口

- `iter_submissions(client, mid, start_page=1, page_retries=3, page_backoff=20.0)`
- `SubmissionPageError.page`：可恢复的失败页码
- `VideoListItem.owner_mid`：接口记录归属 UID

## 更新记录

2026-08-24：增加重复页检测、UID 归属校验、异常响应保护和可恢复页级错误。
