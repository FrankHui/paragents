# Mock Task Scripts

用于在 Paragents CLI/TUI 里模拟不同任务状态（running / completed / failed / waiting input）。

## Python 脚本

- `py_sleep_success.py`
  - 睡眠后成功退出
  - 示例：`python mock_tasks/py_sleep_success.py --seconds 8`

- `py_prompt_input.py`
  - 等待 stdin 输入（可模拟“等待用户输入”）
  - 输入 `YES` 返回成功，否则失败
  - 示例：`python mock_tasks/py_prompt_input.py --timeout 2`

- `py_fail_after_sleep.py`
  - 睡眠后失败退出（非 0）
  - 示例：`python mock_tasks/py_fail_after_sleep.py --seconds 4 --code 3`

- `py_long_logs_for_paging.py`
  - 高密度输出大量日志（stdout + 可选 stderr），用于测试小面板日志翻页
  - 示例：`python mock_tasks/py_long_logs_for_paging.py --lines 180 --delay-ms 10 --stderr-every 9 --tail-sleep 2`

- `prompt_keynode_refresh.txt`
  - 关键节点日志压测模板（非 stdout 刷屏）
  - 通过多轮只读工具调用，产生高密度 `tool call / observation / processing` 事件
  - 适合测试 submit 小面板“打满后刷新机制”
  - 示例（建议用 submit）：
    - `/submit $(cat mock_tasks/prompt_keynode_refresh.txt)`
    - 或 `submit $(cat mock_tasks/prompt_keynode_refresh.txt)`

## Shell 脚本

- `sh_sleep_success.sh`
  - 睡眠后成功
  - 示例：`bash mock_tasks/sh_sleep_success.sh 8`

- `sh_prompt_input.sh`
  - 等待 stdin 输入（`YES` 成功）
  - 示例：`bash mock_tasks/sh_prompt_input.sh 2`

- `sh_fail_after_sleep.sh`
  - 睡眠后失败
  - 示例：`bash mock_tasks/sh_fail_after_sleep.sh 4 2`

## 在你的 Paragents 里如何触发

- 直接语句（会默认转 `/run`）：
  - `python mock_tasks/py_sleep_success.py --seconds 8`

- 显式命令：
  - `/run bash mock_tasks/sh_fail_after_sleep.sh 5 1`

提示：`*_prompt_input*` 脚本会阻塞等待输入，适合测试“长时间运行/等待交互”状态。
另外：`py_long_logs_for_paging.py` 适合测试右侧小面板日志翻页（`PgUp` / `PgDn`）。
