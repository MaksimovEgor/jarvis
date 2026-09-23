// Что делает Джарвис — по-человечески, а не именами инструментов.
const TOOL_LABELS: Record<string, string> = {
  play_music: 'включаю',
  play_radio: 'включаю радио',
  resume_listening: 'включаю',
  web_search: 'ищу в интернете',
  web_extract: 'читаю страницу',
  // Инструменты встроенного агента (AGENT_BACKEND=builtin).
  web_fetch: 'читаю страницу',
  music: 'управляю музыкой',
  terminal: 'выполняю команду',
  execute_code: 'считаю',
  skill_view: 'вспоминаю, как это делать',
  tool_search: 'выбираю инструмент',
  tool_describe: 'выбираю инструмент',
  cronjob_manage: 'планирую задачу',
  send_telegram: 'отправляю в Telegram',
  session_search: 'вспоминаю',
  memory: 'запоминаю',
  read_file: 'читаю файл',
  search_files: 'ищу файлы',
  set_timer: 'ставлю таймер',
  remind: 'ставлю напоминание',
}

export function toolLabel(tool?: string): string {
  if (!tool) return 'думаю'
  const name = tool.replace(/^mcp__\w+?__/, '')
  return TOOL_LABELS[name] ?? name
}
