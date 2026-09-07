export type Lang = 'zh' | 'en' | 'ru'

export const LANGS: { code: Lang; label: string; native: string }[] = [
  { code: 'zh', label: 'Chinese', native: '简体中文' },
  { code: 'en', label: 'English', native: 'English' },
  { code: 'ru', label: 'Russian', native: 'Русский' },
]

type Dict = Record<string, string>

const zh: Dict = {
  'app.title': '课表同步',
  'app.subtitle': '把教务系统的课表，一键装进手机日历',
  'app.badge': '深圳北理莫斯科大学',

  'nav.language': '语言',
  'nav.theme': '主题',
  'theme.auto': '跟随系统',
  'theme.light': '浅色',
  'theme.dark': '深色',

  'login.title': '登录教务系统',
  'login.desc': '使用统一身份认证账号。密码仅用于本次登录，不会保存。',
  'login.username': '学号 / 工号',
  'login.password': '密码',
  'login.captcha': '验证码',
  'login.submit': '登录并读取课表',
  'login.submitting': '正在登录…',
  'login.privacy': '凭据只存在于内存中，服务端不落盘、不记录。',
  'login.logout': '退出',

  'terms.title': '选择学期',
  'terms.desc': '已与教务系统对齐，可导入入学以来的任意学期。',
  'terms.current': '当前',
  'terms.loading': '正在读取学期列表…',
  'terms.empty': '教务系统未返回学期数据。',
  'terms.selected': '已选学期',

  'schedule.title': '课表预览',
  'schedule.events': '个日程',
  'schedule.empty': '该学期没有课程数据。',
  'schedule.teacher': '教师',
  'schedule.location': '教室',
  'schedule.period': '节次',
  'schedule.weeks': '周次',
  'schedule.loading': '正在读取课表…',
  'schedule.noWeeks': '教务系统未提供周次日期，导出将缺少具体日期。',
  'schedule.noSections': '教务系统未提供节次时间，已启用默认时间表。',
  'schedule.noCourses': '该学期没有课程记录。',

  'export.title': '导出到日历',
  'export.desc': '生成 .ics 文件，iOS / Android / 系统日历均可直接导入。',
  'export.reminder': '课前提醒',
  'export.reminder.none': '不提醒',
  'export.minutes': '分钟',
  'export.download': '下载 .ics 文件',
  'export.downloading': '正在生成…',
  'export.hint': '重复导入同一学期不会重复创建，日程会自动更新。',
  'export.howto': '导入方法',

  'day.1': '周一',
  'day.2': '周二',
  'day.3': '周三',
  'day.4': '周四',
  'day.5': '周五',
  'day.6': '周六',
  'day.7': '周日',

  'err.AUTH_FAILED': '账号或密码错误。',
  'err.AUTH_CAPTCHA_REQUIRED': '教务系统要求验证码，请先在浏览器中登录一次再重试。',
  'err.UPSTREAM_ERROR': '教务系统暂时不可用，请稍后重试。',
  'err.SESSION_EXPIRED': '登录已过期，请重新登录。',
  'err.RATE_LIMITED': '请求过于频繁，请稍后再试。',
  'err.NO_DATA': '教务系统未返回数据。',
  'err.TOO_LARGE': '课表数据量过大，无法导出。',
  'err.INTERNAL': '服务内部错误。',
  'err.NETWORK': '无法连接服务，请检查网络。',
  'err.UNKNOWN': '发生未知错误。',
}

const en: Dict = {
  'app.title': 'Schedule Sync',
  'app.subtitle': 'Turn your university timetable into a phone calendar in one click',
  'app.badge': 'SMBU',

  'nav.language': 'Language',
  'nav.theme': 'Theme',
  'theme.auto': 'System',
  'theme.light': 'Light',
  'theme.dark': 'Dark',

  'login.title': 'Sign in to the portal',
  'login.desc': 'Use your campus SSO account. The password is used once and never stored.',
  'login.username': 'Student / Staff ID',
  'login.password': 'Password',
  'login.captcha': 'Captcha',
  'login.submit': 'Sign in and load timetable',
  'login.submitting': 'Signing in…',
  'login.privacy': 'Credentials live in memory only — never written to disk, never logged.',
  'login.logout': 'Sign out',

  'terms.title': 'Select term',
  'terms.desc': 'Matches the portal. Any term since enrolment can be imported.',
  'terms.current': 'Current',
  'terms.loading': 'Loading terms…',
  'terms.empty': 'The portal returned no term data.',
  'terms.selected': 'Selected term',

  'schedule.title': 'Timetable preview',
  'schedule.events': 'events',
  'schedule.empty': 'No courses found for this term.',
  'schedule.teacher': 'Teacher',
  'schedule.location': 'Room',
  'schedule.period': 'Period',
  'schedule.weeks': 'Weeks',
  'schedule.loading': 'Loading timetable…',
  'schedule.noWeeks': 'The portal did not publish week dates; exported events will lack dates.',
  'schedule.noSections': 'The portal did not publish period times; a default grid is applied.',
  'schedule.noCourses': 'No course records for this term.',

  'export.title': 'Export to calendar',
  'export.desc': 'Produces an .ics file that iOS, Android and desktop calendars import directly.',
  'export.reminder': 'Reminder before class',
  'export.reminder.none': 'None',
  'export.minutes': 'min',
  'export.download': 'Download .ics',
  'export.downloading': 'Generating…',
  'export.hint': 'Re-importing the same term updates events instead of duplicating them.',
  'export.howto': 'How to import',

  'day.1': 'Mon',
  'day.2': 'Tue',
  'day.3': 'Wed',
  'day.4': 'Thu',
  'day.5': 'Fri',
  'day.6': 'Sat',
  'day.7': 'Sun',

  'err.AUTH_FAILED': 'Incorrect ID or password.',
  'err.AUTH_CAPTCHA_REQUIRED':
    'The portal requires a captcha. Sign in once in your browser, then retry.',
  'err.UPSTREAM_ERROR': 'The portal is temporarily unavailable. Please retry shortly.',
  'err.SESSION_EXPIRED': 'Session expired. Please sign in again.',
  'err.RATE_LIMITED': 'Too many requests. Please slow down.',
  'err.NO_DATA': 'The portal returned no data.',
  'err.TOO_LARGE': 'This timetable is too large to export.',
  'err.INTERNAL': 'Internal service error.',
  'err.NETWORK': 'Cannot reach the service. Check your connection.',
  'err.UNKNOWN': 'Something went wrong.',
}

const ru: Dict = {
  'app.title': 'Синхронизация расписания',
  'app.subtitle': 'Перенесите расписание из портала в календарь телефона одним кликом',
  'app.badge': 'SMBU',

  'nav.language': 'Язык',
  'nav.theme': 'Тема',
  'theme.auto': 'Как в системе',
  'theme.light': 'Светлая',
  'theme.dark': 'Тёмная',

  'login.title': 'Вход в портал',
  'login.desc':
    'Используйте единую учётную запись. Пароль применяется один раз и не сохраняется.',
  'login.username': 'Номер студента / сотрудника',
  'login.password': 'Пароль',
  'login.captcha': 'Капча',
  'login.submit': 'Войти и загрузить расписание',
  'login.submitting': 'Вход…',
  'login.privacy': 'Данные хранятся только в памяти — не записываются и не логируются.',
  'login.logout': 'Выйти',

  'terms.title': 'Выберите семестр',
  'terms.desc': 'Совпадает с порталом. Можно импортировать любой семестр с момента зачисления.',
  'terms.current': 'Текущий',
  'terms.loading': 'Загрузка семестров…',
  'terms.empty': 'Портал не вернул данные о семестрах.',
  'terms.selected': 'Выбранный семестр',

  'schedule.title': 'Предпросмотр расписания',
  'schedule.events': 'событий',
  'schedule.empty': 'Для этого семестра курсы не найдены.',
  'schedule.teacher': 'Преподаватель',
  'schedule.location': 'Аудитория',
  'schedule.period': 'Пара',
  'schedule.weeks': 'Недели',
  'schedule.loading': 'Загрузка расписания…',
  'schedule.noWeeks': 'Портал не передал даты недель — у событий не будет дат.',
  'schedule.noSections': 'Портал не передал время пар — применена сетка по умолчанию.',
  'schedule.noCourses': 'Для этого семестра нет записей о курсах.',

  'export.title': 'Экспорт в календарь',
  'export.desc':
    'Создаёт файл .ics, который импортируют iOS, Android и настольные календари.',
  'export.reminder': 'Напоминание перед парой',
  'export.reminder.none': 'Без напоминания',
  'export.minutes': 'мин',
  'export.download': 'Скачать .ics',
  'export.downloading': 'Генерация…',
  'export.hint': 'Повторный импорт того же семестра обновляет события, а не дублирует их.',
  'export.howto': 'Как импортировать',

  'day.1': 'Пн',
  'day.2': 'Вт',
  'day.3': 'Ср',
  'day.4': 'Чт',
  'day.5': 'Пт',
  'day.6': 'Сб',
  'day.7': 'Вс',

  'err.AUTH_FAILED': 'Неверный номер или пароль.',
  'err.AUTH_CAPTCHA_REQUIRED':
    'Портал требует капчу. Войдите один раз в браузере и повторите попытку.',
  'err.UPSTREAM_ERROR': 'Портал временно недоступен. Попробуйте позже.',
  'err.SESSION_EXPIRED': 'Сессия истекла. Войдите снова.',
  'err.RATE_LIMITED': 'Слишком много запросов. Подождите.',
  'err.NO_DATA': 'Портал не вернул данные.',
  'err.TOO_LARGE': 'Расписание слишком большое для экспорта.',
  'err.INTERNAL': 'Внутренняя ошибка сервиса.',
  'err.NETWORK': 'Не удаётся подключиться к сервису. Проверьте сеть.',
  'err.UNKNOWN': 'Что-то пошло не так.',
}

const DICTS: Record<Lang, Dict> = { zh, en, ru }

export const HTML_LANG: Record<Lang, string> = { zh: 'zh-CN', en: 'en', ru: 'ru' }

/** Cache compiled dictionaries so re-renders never re-allocate. */
const resolved = new Map<Lang, Dict>()

export function dictionary(lang: Lang): Dict {
  const cached = resolved.get(lang)
  if (cached) return cached
  const dict = DICTS[lang] ?? zh
  resolved.set(lang, dict)
  return dict
}

export function translate(lang: Lang, key: string): string {
  return dictionary(lang)[key] ?? en[key] ?? key
}

export function detectLang(): Lang {
  const stored = safeGet('smbu.lang') as Lang | null
  if (stored && stored in DICTS) return stored
  const nav = typeof navigator !== 'undefined' ? navigator.language.toLowerCase() : ''
  if (nav.startsWith('zh')) return 'zh'
  if (nav.startsWith('ru')) return 'ru'
  return 'en'
}

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
