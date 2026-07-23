# Подсистема параметров

Этот документ описывает все production-сущности пакета
`vrp_parser.parameters`: модели данных, протоколы расширения, readers,
recognizers, validators, реестр и встроенные типы.

Подсистема отвечает на три разных вопроса:

1. Является ли фрагмент шаблона объявлением параметра, например
   `INTEGER<1-15>`?
2. Как извлечь значение параметра из реальной CLI-строки?
3. Подходит ли извлечённое значение типу, прошло ли оно ограничения и каким
   должно быть его нормализованное представление?

Эти обязанности намеренно разделены. Новый тип можно добавить композицией
`ParameterType`, не изменяя lexer шаблонов, граф команд или matcher.

## Публичный импорт

Все публичные сущности экспортируются из `vrp_parser.parameters`:

```python
from vrp_parser.parameters import (
    BoundedDeclarationRecognizer,
    DateTimeValidator,
    DeclarationRecognition,
    DeclarationRecognizer,
    EnumDeclarationRecognizer,
    EnumValidator,
    ExactDeclarationRecognizer,
    HexValidator,
    IntegerValidator,
    MacValidator,
    ParameterDeclaration,
    ParameterDeclarationError,
    ParameterFamily,
    ParameterIssue,
    ParameterReader,
    ParameterRegistryError,
    ParameterResult,
    ParameterStatus,
    ParameterToken,
    ParameterType,
    ParameterTypeRegistry,
    ParameterValidator,
    RemainderReader,
    SingleTokenReader,
    TextValidator,
    TokenStringValidator,
    builtin_parameter_types,
    default_parameter_registry,
)
```

Часть этих объектов нужна только при разработке custom-типа. При обычном
использовании достаточно `default_parameter_registry()` либо вообще не
передавать реестр в `CommandLineParser`: parser создаст стандартный сам.

## Общий поток данных

```text
строка шаблона
    │
    ▼
DeclarationRecognizer.recognize()
    │ DeclarationRecognition
    ▼
ParameterType.recognize()
    │ ParameterDeclaration
    ▼
ParameterReader.read() ───────────── реальная CLI-строка
    │ ParameterToken
    ▼
ParameterValidator.probe()
    │ ParameterResult
    ▼
VALID / INVALID / NOT_APPLICABLE
```

`ParameterTypeRegistry` выбирает нужный `ParameterType` и делегирует ему все
три операции.

## Tri-state: три результата проверки

Каждый validator возвращает не `bool`, а `ParameterResult` с одним из трёх
статусов:

| Статус | Значение | Смысл |
|---|---|---|
| `ParameterStatus.VALID` | `"valid"` | Тип применим, значение прошло проверку. |
| `ParameterStatus.INVALID` | `"invalid"` | Тип применим по форме, но значение нарушает ограничение. Обязательно содержит `ParameterIssue`. |
| `ParameterStatus.NOT_APPLICABLE` | `"not_applicable"` | Значение лексически не относится к этому типу. Это не ошибка валидации данного типа. |

Различие важно при выборе ветки дерева. Например:

```python
registry = default_parameter_registry()

registry.evaluate("INTEGER<1-15>", "7").status
# ParameterStatus.VALID

registry.evaluate("INTEGER<1-15>", "16").status
# ParameterStatus.INVALID: это целое число, но оно больше maximum

registry.evaluate("INTEGER<1-15>", "Vlanif").status
# ParameterStatus.NOT_APPLICABLE: строка вообще не похожа на integer
```

`INVALID` позволяет parser сообщить точную ошибку ограничения вместо
безусловного перехода на менее специфичную ветку. `NOT_APPLICABLE` разрешает
искать другую подходящую ветку.

## Форматы встроенных параметров

`builtin_parameter_types()` создаёт 14 типов:

| Placeholder в шаблоне | `type_id` | Family | Входное значение | `normalized` при успехе |
|---|---|---|---|---|
| `HEX<min-max>` | `hex` | `numeric` | Hex-число, с необязательным `0x`/`0X` | `int` |
| `STRING<min-max>` | `string` | `generic` | Один непустой token без пробелов | Исходный `str` |
| `INTEGER<min-max>` | `integer` | `numeric` | Знаковое десятичное целое | `int` |
| `ENUM{a,b,...}` | `enum` | `enum` | Один из явно перечисленных вариантов, без учёта ASCII-регистра | Вариант в том регистре, в котором он записан в шаблоне |
| `PASSWORDEX<min-max>` | `passwordex` | `generic` | Один непустой token без пробелов | Исходный `str` |
| `H-H-H` | `mac` | `structured` | Три группы по 1–4 hex-цифры | Три lowercase-группы по 4 цифры |
| `TEXT<min-max>` | `text` | `remainder` | Непустой остаток строки с длиной в заданном диапазоне | Исходный `str` |
| `YYYY/MM/DD` | `date-slash` | `structured` | Календарная дата точной ширины | Исходный `str` |
| `YYYY-MM-DD` | `date-iso` | `structured` | Календарная дата точной ширины | Исходный `str` |
| `MM-DD` | `month-day` | `structured` | Месяц и день точной ширины | Исходный `str` |
| `MM-DD-YYYY` | `date-us` | `structured` | Календарная дата точной ширины | Исходный `str` |
| `YYYY/MM/DD,HH:MM:SS` | `datetime-slash` | `structured` | Дата и время точной ширины | Исходный `str` |
| `HH:MM:SS` | `time-seconds` | `structured` | Время точной ширины | Исходный `str` |
| `<hh:mm>` | `time` | `structured` | Время точной ширины | Исходный `str` |

Важные детали форматов:

- `min` и `max` у `INTEGER` — десятичные числовые границы.
- `min` и `max` у `HEX` записываются и интерпретируются в hex. Например,
  `HEX<80-FD>` означает диапазон от `0x80` до `0xFD`.
- `min` и `max` у `STRING`, `PASSWORDEX` и `TEXT` — количество Python-символов
  согласно `len(value)`, а не байтов.
- `PASSWORDEX` в этой подсистеме проверяет только token и длину. Специальной
  криптографической обработки или проверки сложности пароля нет.
- Для `MM-DD` календарная корректность проверяется с условным високосным
  2000 годом, поэтому `02-29` допустимо.
- `TEXT<min-max>` на уровне `RemainderReader` считывает весь переданный ему
  остаток, включая внутренние и завершающие пробелы. Поэтому конструкция
  `description TEXT<1-80>` принимает многословное описание как один параметр.
  Публичный `CommandLineParser` перед matching удаляет завершающий whitespace
  всей команды. Ограничение верхнего уровня применяется только к `TEXT`,
  сопоставляемому в позиции `0` на bare/root route: такая ветвь принимает лишь
  CLI-строки, начинающиеся с `!`. Вложенный `TEXT`, которому предшествует
  keyword, этого ограничения не имеет. Правило реализовано вне parameter
  validator. В bare/root-варианте ведущий `!` входит в `raw` и учитывается
  функцией `len()` при проверке bounds.
- `X.X.X.X`, `X:X::X:X`, их prefix-варианты и
  `STRING<min-max>/<min-max>` не входят во встроенный реестр.

## `models.py`: модели и интерфейсы

Все dataclass-модели в этом модуле объявлены как `frozen=True, slots=True`.
После создания их поля нельзя изменять.

### `ParameterStatus`

```python
class ParameterStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    VALID = "valid"
    INVALID = "invalid"
```

Строковый enum результата `probe()`. Поскольку это `StrEnum`, его значения
можно сериализовать как обычные строки.

### `ParameterFamily`

```python
class ParameterFamily(StrEnum):
    ENUM = "enum"
    STRUCTURED = "structured"
    NUMERIC = "numeric"
    GENERIC = "generic"
    REMAINDER = "remainder"
```

Широкая категория поведения типа. Matcher использует family для
детерминированного предпочтения более специфичных параметров:
`ENUM`, затем `STRUCTURED`, `NUMERIC`, `GENERIC`, `REMAINDER`. Literal-ветка
команды находится ещё выше и не является `ParameterFamily`.

- `ENUM` — конечный набор известных значений.
- `STRUCTURED` — значение с фиксированной структурой, например дата или MAC.
- `NUMERIC` — числа и числовые диапазоны.
- `GENERIC` — произвольный token.
- `REMAINDER` — оставшаяся часть строки.

`family` — часть контракта custom-типа: неверно выбранная family может изменить
то, какая ветка будет считаться более специфичной.

### `ParameterIssue`

Конструктор:

```python
ParameterIssue(
    code: str,
    message: str,
    expected: str | None = None,
    actual: str | None = None,
)
```

Машиночитаемое описание ошибки:

- `code` — стабильный короткий идентификатор причины;
- `message` — текст для человека;
- `expected` — необязательное описание ожидаемого ограничения или формата;
- `actual` — необязательное фактическое значение либо его измеримая
  характеристика.

Объект не навязывает список кодов. Встроенные validators используют коды,
перечисленные в разделе об ошибках ниже.

### `ParameterResult`

Конструктор:

```python
ParameterResult(
    status: ParameterStatus,
    normalized: object | None = None,
    issue: ParameterIssue | None = None,
)
```

Поля:

- `status` — один из трёх результатов;
- `normalized` — нормализованное значение для `VALID`;
- `issue` — описание причины для `INVALID`.

Обычно прямой конструктор не нужен: безопаснее пользоваться classmethod’ами.

#### `__post_init__() -> None`

Проверяет инварианты после создания:

- `INVALID` обязан содержать `issue`;
- `VALID` и `NOT_APPLICABLE` не могут содержать `issue`;
- `NOT_APPLICABLE` не может содержать `normalized`.

Нарушение приводит к `ValueError`. Текущий контракт допускает
`ParameterResult.success(None)`: это всё равно `VALID`; определять успех по
полю `normalized` нельзя.

#### `applicable: bool`

Read-only property. Возвращает `True` для `VALID` и `INVALID`, `False` только
для `NOT_APPLICABLE`.

#### `valid: bool`

Read-only property. Возвращает `True` только для `VALID`.

#### `message: str | None`

Read-only property. Возвращает `issue.message`, если issue есть, иначе `None`.
По инвариантам непустое сообщение бывает только у `INVALID`.

#### `not_applicable() -> ParameterResult`

Classmethod-конструктор:

```python
ParameterResult.not_applicable()
```

Создаёт результат:

```python
ParameterResult(
    status=ParameterStatus.NOT_APPLICABLE,
    normalized=None,
    issue=None,
)
```

#### `success(normalized: object) -> ParameterResult`

Classmethod-конструктор успешного результата. `normalized` может иметь любой
тип, включая `str`, `int`, `bool` и `None`.

```python
ParameterResult.success(42)
```

#### `failure(...) -> ParameterResult`

Сигнатура:

```python
ParameterResult.failure(
    code: str,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
) -> ParameterResult
```

Создаёт `INVALID` и вложенный `ParameterIssue`. `expected` и `actual`
keyword-only.

### `ParameterDeclaration`

Конструктор:

```python
ParameterDeclaration(
    type_id: str,
    source: str,
    start: int,
    end: int,
    minimum: int | None = None,
    maximum: int | None = None,
    choices: tuple[str, ...] = (),
    metadata: tuple[tuple[str, str], ...] = (),
)
```

Это уже распознанный placeholder внутри полного шаблона команды.

- `type_id` — ID зарегистрированного `ParameterType`;
- `source` — точная подстрока placeholder’а;
- `start` — индекс первого символа в полном шаблоне;
- `end` — исключающая правая граница;
- `minimum`, `maximum` — разобранные границы, если они есть;
- `choices` — варианты enum;
- `metadata` — неизменяемые custom-атрибуты в виде пар ключ/значение.

Индексы считаются в Python-символах, не в байтах. Span полуоткрытый:
`pattern[start:end] == source`.

#### `__post_init__() -> None`

Проверяет:

- `0 <= start <= end`;
- `end - start == len(source)`.

При нарушении выбрасывает `ValueError`. Метод не проверяет, что `type_id`
зарегистрирован, что `minimum <= maximum` или что span действительно относится
к конкретной внешней строке: это ответственность источника объекта.

### `DeclarationRecognition`

Конструктор:

```python
DeclarationRecognition(
    end: int,
    minimum: int | None = None,
    maximum: int | None = None,
    choices: tuple[str, ...] = (),
    metadata: tuple[tuple[str, str], ...] = (),
)
```

Промежуточный результат recognizer’а. Он ещё не содержит `type_id`, `source`
и `start`: их добавляет `ParameterType.recognize()`.

- `end` — исключающая позиция конца объявления в полном шаблоне;
- остальные поля переносятся в `ParameterDeclaration` без изменений.

У класса нет дополнительной runtime-валидации полей. Custom recognizer должен
возвращать корректный `end`.

### `ParameterToken`

Конструктор:

```python
ParameterToken(
    raw: str,
    start: int,
    end: int,
    next_position: int,
)
```

Результат чтения значения из реальной CLI-строки:

- `raw` — извлечённый текст;
- `start`, `end` — полуоткрытый span значения;
- `next_position` — позиция, с которой matcher должен продолжить чтение.

Обычные readers возвращают `next_position == end`. Custom reader может
пропустить собственный разделитель и вернуть позицию больше `end`.

#### `__post_init__() -> None`

Проверяет:

- `0 <= start <= end`;
- `end - start == len(raw)`;
- `next_position >= end`.

Иначе выбрасывает `ValueError`. Как и `ParameterDeclaration`, объект сам не
хранит исходную строку и не может проверить соответствие span этой строке.

### `DeclarationRecognizer`

Structural-typing protocol. Наследоваться от него необязательно: достаточно
метода с совместимой сигнатурой.

```python
recognize(
    pattern: str,
    position: int,
) -> DeclarationRecognition | None
```

Вход:

- `pattern` — полный шаблон команды;
- `position` — точная позиция, с которой ожидается placeholder.

Результат:

- `DeclarationRecognition`, если объявление начинается ровно в `position`;
- `None`, если recognizer к этому фрагменту неприменим;
- `ParameterDeclarationError`, если фрагмент начинается как известное этому
  recognizer’у объявление, но синтаксически испорчен.

### `ParameterReader`

Structural-typing protocol:

```python
read(text: str, position: int) -> ParameterToken | None
```

`text` — полная реальная CLI-команда, `position` — позиция начала поиска.
Возвращает token либо `None`, когда значения больше нет.

Reader не валидирует смысл значения. Он лишь определяет его границы.

### `ParameterValidator`

Structural-typing protocol:

```python
probe(
    raw: str,
    declaration: ParameterDeclaration,
) -> ParameterResult
```

Validator обязан следовать tri-state-контракту:

- правильное значение → `success(...)`;
- форма относится к типу, но ограничение нарушено → `failure(...)`;
- форма вообще не относится к типу → `not_applicable()`.

### `ParameterType`

Конструктор:

```python
ParameterType(
    type_id: str,
    family: ParameterFamily,
    declaration_recognizer: DeclarationRecognizer,
    reader: ParameterReader,
    validator: ParameterValidator,
)
```

Композиционный корень одного типа:

- recognizer понимает его запись в шаблоне;
- reader выделяет значение из CLI;
- validator проверяет и нормализует значение;
- family сообщает matcher’у специфичность.

Сам dataclass не проверяет формат `type_id`; это делает
`ParameterTypeRegistry.register()`.

#### `recognize(pattern: str, position: int = 0) -> ParameterDeclaration | None`

Вызывает `declaration_recognizer.recognize(pattern, position)`.

- При `None` возвращает `None`.
- При успехе присоединяет собственный `type_id`, вычисляет
  `source=pattern[position:recognized.end]` и переносит bounds, choices,
  metadata в неизменяемый `ParameterDeclaration`.
- Ошибки recognizer’а не перехватывает.

#### `read(text: str, position: int = 0) -> ParameterToken | None`

Делегирует чтение `reader.read(text, position)` без дополнительной обработки.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

Если `declaration.type_id != self.type_id`, validator не вызывается и
возвращается `NOT_APPLICABLE`. Иначе вызов делегируется validator’у.

## `readers.py`: чтение CLI-значений

### `_skip_whitespace(text: str, position: int) -> int`

Private module-level helper. Проверяет, что
`0 <= position <= len(text)`, иначе выбрасывает `ValueError` с сообщением
`parameter position is outside the command line`.

Затем пропускает все символы, для которых Python `str.isspace()` возвращает
`True`, и возвращает позицию первого непробельного символа либо `len(text)`.

### `SingleTokenReader`

Reader одного непробельного token.

#### `read(text: str, position: int) -> ParameterToken | None`

1. Пропускает leading whitespace через `_skip_whitespace()`.
2. Если достигнут конец строки, возвращает `None`.
3. Читает до следующего Unicode-whitespace или конца строки.

Пунктуация не имеет специального смысла и остаётся частью `raw`.

```python
token = SingleTokenReader().read("  Vlanif100 next", 0)
# ParameterToken(raw="Vlanif100", start=2, end=11, next_position=11)
```

Неверная позиция приводит к `ValueError` из `_skip_whitespace()`.

### `RemainderReader`

Reader всего непустого остатка строки.

#### `read(text: str, position: int) -> ParameterToken | None`

Пропускает whitespace перед значением. Если после него ничего нет, возвращает
`None`. Иначе читает до физического конца `text`.

Внутренние и завершающие пробелы входят в `raw`:

```python
token = RemainderReader().read("  description with spaces  ", 0)
# raw == "description with spaces  "
# start == 2
# end == next_position == len(text)
```

## `recognizers.py`: объявления в шаблонах

### `_is_boundary(pattern: str, end: int) -> bool`

Private helper, проверяющий правую границу placeholder’а. Возвращает `True`,
если `end` находится:

- ровно в конце шаблона;
- перед whitespace;
- перед одним из символов `|`, `}`, `]`, `*`, `&`.

Это предотвращает частичное распознавание: recognizer `YES` не должен принять
начало литерала `YES-NO`.

### `_ascii_lower(value: str) -> str`

Private helper ASCII-case-folding. Заменяет только `A`–`Z` на `a`–`z`.
Не выполняет Unicode case folding. Используется для проверки уникальности
enum-вариантов.

### `ExactDeclarationRecognizer`

Конструктор:

```python
ExactDeclarationRecognizer(
    placeholder: str,
    minimum: int | None = None,
    maximum: int | None = None,
    metadata: tuple[tuple[str, str], ...] = (),
)
```

Распознаёт одну точную запись placeholder’а. Дополнительные поля позволяют
прикрепить к фиксированной записи bounds или metadata.

#### `recognize(pattern: str, position: int) -> DeclarationRecognition | None`

Успех возможен, только если:

1. `pattern.startswith(placeholder, position)`;
2. сразу после placeholder находится допустимая `_is_boundary()`.

При успехе возвращает `DeclarationRecognition` с
`end=position + len(placeholder)` и настроенными `minimum`, `maximum`,
`metadata`. Иначе возвращает `None`. Этот recognizer не выбрасывает
`ParameterDeclarationError`.

### `BoundedDeclarationRecognizer`

Конструктор:

```python
BoundedDeclarationRecognizer(
    name: str,
    base: int = 10,
    allow_negative: bool = False,
)
```

Распознаёт `NAME<minimum-maximum>`.

- `name` — регистрозависимый префикс, например `INTEGER`;
- `base` — система счисления границ; поддержаны только `10` и `16`;
- `allow_negative` — для base 10 разрешает знаки `+` и `-` в границах.

Этот флаг относится к синтаксису границ объявления. Синтаксис реального
значения определяет отдельный validator.

#### `recognize(pattern: str, position: int) -> DeclarationRecognition | None`

Поведение:

- если в `position` нет точного префикса `f"{name}<"`, возвращает `None`;
- ищет первый закрывающий `>`;
- разбирает две границы, разделённые `-`;
- конвертирует их в `int`;
- требует `minimum <= maximum`;
- требует корректную правую boundary.

При успехе возвращает `DeclarationRecognition(end, minimum, maximum)`.

Ошибки `ParameterDeclarationError`:

- `unclosed NAME declaration` — нет `>`;
- `invalid bounds in NAME declaration` — формат bounds не соответствует base;
- `minimum is greater than maximum in NAME declaration`.

Если само объявление корректно, но после `>` нет boundary, возвращается `None`,
а не ошибка.

#### `_number_pattern() -> str`

Private method, возвращающий regex одного bound:

- base 10: `[0-9]+`, либо `[+-]?[0-9]+` при `allow_negative=True`;
- base 16: необязательный `0x`/`0X` и одна или больше hex-цифр.

Для иной base выбрасывает `ValueError("unsupported declaration bound base: …")`.

#### `_parse_number(value: str) -> int`

Private method. Вызывает `int(value, self.base)` и возвращает Python `int`.

### `EnumDeclarationRecognizer`

Конструктор без аргументов:

```python
EnumDeclarationRecognizer()
```

#### `recognize(pattern: str, position: int) -> DeclarationRecognition | None`

Распознаёт полную регистрозависимую запись `ENUM{choice1,choice2,...}`.

Алгоритм:

1. Проверяет точный префикс `ENUM{`.
2. Ищет первый `}`.
3. Проверяет boundary после `}`.
4. Разделяет содержимое по запятым и удаляет whitespace по краям каждого
   варианта.
5. Разрешает одну завершающую запятую.
6. Проверяет непустоту и case-insensitive уникальность вариантов.

Возвращает `DeclarationRecognition(end=end, choices=tuple(choices))`.

Ошибки `ParameterDeclarationError`:

- `unclosed ENUM declaration`;
- `ENUM must contain non-empty choices`;
- `ENUM cannot contain the abbreviated '...' choice`;
- `ENUM choices must be unique (case-insensitive)`.

Сравнение уникальности использует только ASCII-case-folding. Порядок и
исходный регистр вариантов сохраняются.

## `validators.py`: проверка и нормализация

### `_bounded_number(value: int, declaration: ParameterDeclaration) -> ParameterResult | None`

Private helper числовых границ:

- ниже `minimum` → `INVALID`, code `below_minimum`;
- выше `maximum` → `INVALID`, code `above_maximum`;
- в диапазоне либо граница отсутствует → `None`.

`expected` содержит `>= minimum` или `<= maximum`, `actual` — десятичную строку
нормализованного `int`.

### `_bounded_length(value: str, declaration: ParameterDeclaration) -> ParameterResult | None`

Private helper длины:

- короче `minimum` → `INVALID`, code `too_short`;
- длиннее `maximum` → `INVALID`, code `too_long`;
- допустимая длина → `None`.

Длина вычисляется через `len(value)`. `actual` содержит длину как десятичную
строку, а не исходное значение.

### `_ascii_lower(value: str) -> str`

Private ASCII-only приведение `A`–`Z` к `a`–`z`. Используется
`EnumValidator`. Unicode-регистр не нормализуется.

### `IntegerValidator`

Конструктор без аргументов.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

- Не соответствует `[+-]?[0-9]+` → `NOT_APPLICABLE`.
- Соответствует, но `int(raw, 10)` вне bounds → `INVALID` от
  `_bounded_number()`.
- Допустимо → `VALID`, `normalized` — Python `int`.

Примеры: `+7` и `-15` лексически применимы; `1.0` и `12ms` неприменимы.

### `HexValidator`

Конструктор без аргументов.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

- Не соответствует `(?:0[xX])?[0-9A-Fa-f]+` → `NOT_APPLICABLE`.
- Hex-число вне bounds → `INVALID`.
- Допустимо → `VALID`, `normalized=int(raw, 16)`.

Префикс `0x` необязателен. Поэтому raw `10` нормализуется в `16`, а не `10`.

### `TokenStringValidator`

Используется `STRING` и `PASSWORDEX`.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

- Пустой raw или наличие любого `str.isspace()` → `INVALID`,
  code `invalid_token`;
- token короче/длиннее bounds → `INVALID` с `too_short`/`too_long`;
- иначе → `VALID`, `normalized` равен исходному raw.

Этот validator считает пробельную строку применимой, но некорректной. В
обычном parser-потоке `SingleTokenReader` заранее выделяет один token, однако
правило важно при прямом вызове `evaluate()` или custom reader.

### `TextValidator`

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

Не предъявляет требований к структуре и whitespace. Проверяет только
`_bounded_length()`:

- нарушение длины → `INVALID`;
- иначе → `VALID` с исходным raw.

Validator сам не проверяет начальный `!`: это правило command parser’а только
для bare/root `TEXT<min-max>`, сопоставляемого в позиции `0`. В паттерне
`description TEXT<1-80>` validator обычно получает многословный remainder без
keyword `description`.

### `EnumValidator`

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

Ищет raw в `declaration.choices` без учёта ASCII-регистра.

- Совпадение → `VALID`; `normalized` — исходный вариант из declaration.
- Нет совпадения → `NOT_APPLICABLE`.

`INVALID` этот validator не возвращает.

```python
registry.evaluate("ENUM{Eth-trunk,Vlanif,}", "vLaNiF").normalized
# "Vlanif"
```

### `DateTimeValidator`

Конструктор:

```python
DateTimeValidator(
    expression: str,
    datetime_format: str,
    description: str,
    prefix_for_parsing: str = "",
)
```

- `expression` — regex полной лексической формы;
- `datetime_format` — формат `datetime.strptime`;
- `description` — текст ожидаемого формата для issue;
- `prefix_for_parsing` — добавка только перед календарной проверкой. Например,
  `MM-DD` проверяется как `"2000-" + raw`.

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

`declaration` намеренно не используется.

1. `re.fullmatch(expression, raw)` не совпал → `NOT_APPLICABLE`.
2. Форма совпала, но `datetime.strptime(prefix + raw, format)` отверг
   календарное значение → `_failure(raw)`.
3. Значение корректно → `VALID`, `normalized` равен исходному raw.

Таким образом, `2025-2-3` для ISO-типа — `NOT_APPLICABLE` из-за ширины, а
`2025-02-30` — `INVALID`, потому что форма правильна, но даты не существует.

#### `_failure(raw: str) -> ParameterResult`

Private method. Создаёт:

```python
ParameterResult.failure(
    "invalid_datetime",
    f"value must be a valid {description}",
    expected=description,
    actual=raw,
)
```

### `MacValidator`

#### `probe(raw: str, declaration: ParameterDeclaration) -> ParameterResult`

`declaration` не используется. Полный допустимый формат:

```text
1–4 hex-цифры - 1–4 hex-цифры - 1–4 hex-цифры
```

Результаты:

- корректная форма → `VALID`; каждая группа приводится к lowercase и
  дополняется нулями слева до четырёх символов;
- строка состоит из hex-символов/дефисов и содержит дефис, но группировка
  неверна → `INVALID`, code `invalid_mac`;
- строка содержит иные символы либо вообще не содержит дефиса →
  `NOT_APPLICABLE`.

Пример: `1-aB-CD09` нормализуется в `0001-00ab-cd09`.

## `registry.py`: `ParameterTypeRegistry`

### `__init__(parameter_types: tuple[ParameterType, ...] = ()) -> None`

```python
ParameterTypeRegistry(
    parameter_types: tuple[ParameterType, ...] = (),
)
```

Создаёт изменяемый реестр и последовательно регистрирует переданные типы через
`register()`. Поэтому к constructor input применяются те же проверки ID и
дубликатов.

Порядок вставки сохраняется в `parameter_types`, но распознавание объявления
выбирает самое длинное совпадение, а не первый тип.

### `is_frozen: bool`

Read-only property. `False` у нового реестра, `True` после `freeze()`.

### `parameter_types: tuple[ParameterType, ...]`

Read-only property. Возвращает новый tuple зарегистрированных объектов в
порядке регистрации. Изменение tuple невозможно, но сами стратегии внутри
`ParameterType` должны проектироваться как stateless или безопасные для
совместного использования.

### `register(parameter_type: ParameterType) -> ParameterTypeRegistry`

Регистрирует тип и возвращает тот же `self`, что позволяет chaining:

```python
registry.register(first).register(second)
```

Требования к `type_id`:

```text
[a-z][a-z0-9-]*
```

То есть первая буква lowercase ASCII, затем lowercase ASCII, цифры или дефис.

`ParameterRegistryError` выбрасывается, если:

- реестр frozen;
- ID имеет неверный формат;
- такой ID уже зарегистрирован.

Метод не проверяет пересечение синтаксиса разных recognizers. Неоднозначность
обнаружится в `recognize()`.

### `freeze() -> ParameterTypeRegistry`

Запрещает последующие `register()` и возвращает тот же `self`. Повторный
`freeze()` безопасен.

Операция не создаёт глубоких копий типов.

### `clone() -> ParameterTypeRegistry`

Создаёт новый **незамороженный** реестр с теми же `ParameterType`-объектами и
тем же порядком. Состояние `is_frozen` исходного реестра не переносится.

`CommandLineParser` использует именно `source_registry.clone().freeze()`.
Поэтому дальнейшая регистрация в исходном реестре не изменит уже созданный
parser.

### `get(type_id: str) -> ParameterType | None`

Возвращает зарегистрированный тип по точному ID или `None`.

### `family_of(type_id: str) -> ParameterFamily | None`

Возвращает family зарегистрированного типа либо `None` для неизвестного ID.

### `recognize(pattern: str, position: int = 0) -> ParameterDeclaration | None`

Запускает recognizer каждого зарегистрированного типа точно в `position`.

- Нет совпадений → `None`.
- Есть совпадения разной длины → выбирается объявление с наибольшим `end`.
- Несколько совпадений с одинаковым наибольшим `end` →
  `ParameterRegistryError("ambiguous declaration at character …")`.

Longest-match нужен, например, чтобы точный
`YYYY/MM/DD,HH:MM:SS` не был ошибочно сокращён до другого префикса.

`ParameterDeclarationError` от конкретного recognizer’а проходит наружу.
Позиция должна быть допустима для используемых recognizers; registry отдельно
её не валидирует.

### `read(declaration, text, position=0) -> ParameterToken | None`

Полная сигнатура:

```python
read(
    declaration: ParameterDeclaration,
    text: str,
    position: int = 0,
) -> ParameterToken | None
```

Находит тип по `declaration.type_id` и делегирует `ParameterType.read()`.
Неизвестный type ID → `None`.

### `probe(raw, declaration) -> ParameterResult`

```python
probe(
    raw: str,
    declaration: ParameterDeclaration,
) -> ParameterResult
```

Находит тип и делегирует `ParameterType.probe()`. Неизвестный type ID →
`NOT_APPLICABLE`.

### `evaluate(declaration_pattern, raw) -> ParameterResult`

```python
evaluate(
    declaration_pattern: str,
    raw: str,
) -> ParameterResult
```

Удобный объединённый вызов для одного полного placeholder’а:

1. `recognize(declaration_pattern, position=0)`;
2. проверка, что declaration заканчивается ровно в конце строки;
3. `probe(raw, declaration)`.

Результаты:

- полный известный placeholder и значение → результат validator’а;
- неизвестный либо распознанный только частично placeholder →
  `NOT_APPLICABLE`;
- `ParameterDeclarationError` → `INVALID` с code
  `invalid_declaration`, `message=error.message`,
  `actual=declaration_pattern`.

`ParameterRegistryError`, включая неоднозначность recognizers, не
перехватывается.

## `errors.py`: исключения определения типов

### `ParameterDeclarationError`

Наследуется от `ValueError`.

#### `__init__(message: str, start: int, end: int) -> None`

```python
ParameterDeclarationError(
    message: str,
    start: int,
    end: int,
)
```

Публичные атрибуты:

- `message` — причина без координат;
- `start`, `end` — span проблемного объявления.

Строковое представление формируется как:

```text
{message} at characters {start}:{end}
```

Исключение означает: recognizer узнал начало своего placeholder’а, но
объявление синтаксически испорчено.

### `ParameterRegistryError`

Наследуется от `ValueError` и не добавляет собственных полей или методов.
Используется для неверной регистрации, изменения frozen-реестра и
неоднозначного распознавания объявления.

## `builtins.py`: сборка стандартного реестра

### `_date_time_types(reader: SingleTokenReader) -> tuple[ParameterType, ...]`

Private factory семи date/time-типов. Все созданные типы:

- относятся к `ParameterFamily.STRUCTURED`;
- используют переданный `SingleTokenReader`;
- распознаются через `ExactDeclarationRecognizer`;
- проверяются отдельными настроенными экземплярами `DateTimeValidator`.

Возвращает типы в порядке:

1. `date-slash`;
2. `date-iso`;
3. `month-day`;
4. `date-us`;
5. `datetime-slash`;
6. `time-seconds`;
7. `time`.

### `builtin_parameter_types() -> tuple[ParameterType, ...]`

Создаёт свежий tuple всех 14 встроенных определений. Новый
`SingleTokenReader` совместно используется token-based типами внутри одного
вызова; `TEXT` распознаётся через `BoundedDeclarationRecognizer("TEXT")` и
получает `RemainderReader`.

Порядок tuple:

1. `hex`;
2. `string`;
3. `integer`;
4. `enum`;
5. `passwordex`;
6. `mac`;
7. `text`;
8. семь date/time-типов в порядке `_date_time_types()`.

Функция не возвращает singleton: каждый вызов создаёт свежие immutable
`ParameterType`-объекты и stateless стратегии.

### `default_parameter_registry() -> ParameterTypeRegistry`

Эквивалент:

```python
ParameterTypeRegistry(builtin_parameter_types())
```

Возвращает новый **изменяемый** реестр. Его можно расширить перед передачей в
`CommandLineParser`.

## Встроенные коды ошибок

| Code | Источник | Когда возникает | `actual` |
|---|---|---|---|
| `invalid_declaration` | `ParameterTypeRegistry.evaluate()` | Известное объявление синтаксически испорчено | Строка объявления |
| `below_minimum` | `_bounded_number()` | Число меньше minimum | Нормализованное число в decimal |
| `above_maximum` | `_bounded_number()` | Число больше maximum | Нормализованное число в decimal |
| `too_short` | `_bounded_length()` | Длина меньше minimum | Длина |
| `too_long` | `_bounded_length()` | Длина больше maximum | Длина |
| `invalid_token` | `TokenStringValidator` | Пустое значение или whitespace внутри | Исходное значение |
| `invalid_datetime` | `DateTimeValidator` | Форма правильная, календарное значение невозможно | Исходное значение |
| `invalid_mac` | `MacValidator` | Строка похожа на MAC, но имеет неверные группы | Исходное значение |

Custom validator может определять собственные стабильные коды.

## Пример custom-типа

Ниже добавлен placeholder `BOOLEAN`, который принимает `yes` и `no` и
нормализует их в Python `bool`.

```python
from vrp_parser import CommandLineParser
from vrp_parser.parameters import (
    ExactDeclarationRecognizer,
    ParameterDeclaration,
    ParameterFamily,
    ParameterResult,
    ParameterType,
    SingleTokenReader,
    default_parameter_registry,
)


class BooleanValidator:
    def probe(
        self,
        raw: str,
        declaration: ParameterDeclaration,
    ) -> ParameterResult:
        del declaration
        normalized = raw.lower()
        if normalized == "yes":
            return ParameterResult.success(True)
        if normalized == "no":
            return ParameterResult.success(False)
        if raw.isalpha():
            return ParameterResult.failure(
                "invalid_boolean",
                "value must be yes or no",
                expected="yes | no",
                actual=raw,
            )
        return ParameterResult.not_applicable()


registry = default_parameter_registry()
registry.register(
    ParameterType(
        type_id="boolean",
        family=ParameterFamily.ENUM,
        declaration_recognizer=ExactDeclarationRecognizer("BOOLEAN"),
        reader=SingleTokenReader(),
        validator=BooleanValidator(),
    )
)

parser = CommandLineParser(
    {"commands": ["feature BOOLEAN"]},
    parameter_types=registry,
)

enabled = parser.parse("feature yes")
disabled = parser.parse("feature no")
invalid = parser.parse("feature maybe")
```

Правила проектирования custom-типа:

1. Используйте уникальный `type_id` формата `[a-z][a-z0-9-]*`.
2. Recognizer должен принимать placeholder только с точной `position`.
3. Reader должен возвращать корректные char-spans и позицию продолжения.
4. Validator должен различать `INVALID` и `NOT_APPLICABLE`.
5. Возвращайте стабильный тип `normalized`, чтобы вызывающему коду не
   приходилось угадывать формат.
6. Выберите family по специфичности значения, а не по названию placeholder’а.
7. Зарегистрируйте все custom-типы до создания `CommandLineParser`: parser
   клонирует и замораживает полученный реестр.

## Самостоятельная проверка параметра

Для проверки типа без построения parser’а используйте `evaluate()`:

```python
from vrp_parser.parameters import ParameterStatus, default_parameter_registry

registry = default_parameter_registry()
result = registry.evaluate("H-H-H", "1-aB-CD09")

assert result.status is ParameterStatus.VALID
assert result.normalized == "0001-00ab-cd09"
assert result.issue is None
```

Для поэтапной интеграции доступны отдельные операции:

```python
declaration = registry.recognize(
    "set preference INTEGER<1-15>",
    position=len("set preference "),
)
assert declaration is not None

token = registry.read(declaration, "set preference 10", len("set preference "))
assert token is not None

result = registry.probe(token.raw, declaration)
assert result.valid
assert result.normalized == 10
```
