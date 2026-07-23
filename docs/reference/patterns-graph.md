# Язык паттернов, компиляция и общий граф команд

Этот документ описывает внутренний путь команды от строки в
`commands.json` до неизменяемого общего графа. Он охватывает модули:

- `vrp_parser.compiler`;
- `vrp_parser.errors`;
- `vrp_parser.patterns`;
- `vrp_parser.graph`.

Матчинг конкретной CLI-строки выполняется следующим слоем проекта. Здесь
рассматриваются синтаксис паттернов, AST, проверка проектных ограничений,
линеаризация вариантов и сохранение provenance — связи маршрута с исходным
паттерном.

## Общая схема

```text
tuple[str, ...]
    │
    ▼
PatternLexer ──► tuple[Token, ...]
    │
    ▼
PatternParser ──► Sequence (AST)
    │
    ▼
RuntimePatternPolicy
    │
    ▼
PatternSource
    │
    ▼
RouteExpander ──► LinearRoute
    │
    ▼
GraphStepFactory ──► GraphStep с семантическим key
    │
    ▼
CommandGraphBuilder ──► CommandGraph
```

`PatternCompiler` координирует весь этот процесс. Он обрабатывает все
исходные строки, накапливает все ошибки и строит граф только тогда, когда
валидны все паттерны.

## Формат входного документа

Публичный парсер получает JSON-совместимый объект:

```json
{
  "commands": [
    "#",
    "TEXT<1-4096>",
    "description TEXT<1-80>",
    "interface STRING<1-63>",
    "peer X.X.X.X",
    "peer X:X::X:X",
    "network X:X::X:X/M",
    "interface { STRING<1-63> | ENUM{Eth-trunk,Vlanif,Vbdif} STRING<1-63> }"
  ]
}
```

Проверка формы JSON-документа выполняется публичным API до вызова
`PatternCompiler`. На вход `PatternCompiler.compile()` уже передаётся
`tuple[str, ...]`.

Основные требования к документу:

- корень — объект;
- поле `commands` — непустой массив;
- каждый элемент — непустая строка;
- порядок элементов значим: он определяет `pattern_index` и порядок
  представительного результата при нескольких совпадениях;
- одинаковые строки допустимы и остаются отдельными источниками.

Нарушение формы документа приводит к `PatternDocumentError`. Синтаксические
и семантические ошибки внутри строк объединяются в
`PatternCompilationError`.

## Грамматика паттернов

Ниже приведена упрощённая EBNF фактически реализованного frontend:

```ebnf
pattern       = root_item, { root_item } ;
root_item     = atom, [ repeat ] ;
atom          = literal
              | parameter
              | group
              | "*"
              | "|" ;             (* "|" — литерал только в корне *)

group         = required_group | optional_group ;
required_group = "{", alternative, { "|", alternative }, "}", [ "*" ] ;
optional_group = "[", alternative, { "|", alternative }, "]", [ "*" ] ;
alternative   = group_item, { group_item } ;
group_item    = group_atom, [ repeat ] ;
group_atom    = literal | parameter | group | "*" ;

repeat        = "&<", unsigned_integer, "-", unsigned_integer, ">" ;
```

Лексер игнорирует пробельные символы между элементами. Поэтому `}*` и
`} *` технически распознаются одинаково. Каноническая запись в
`commands.json` — с пробелом: `} *` и `] *`.

### Семантика групп

| Синтаксис | `GroupMode` | Значение |
|---|---|---|
| `{ x \| y }` | `REQUIRED_ONE` | требуется ровно одна альтернатива |
| `[ x \| y ]` | `OPTIONAL_ONE` | выбирается одна альтернатива или группа пропускается |
| `{ x \| y } *` | `REQUIRED_SET` | выбирается от одной до всех альтернатив, без повторного использования одной ветви |
| `[ x \| y ] *` | `OPTIONAL_SET` | выбирается от нуля до всех альтернатив, без повторного использования одной ветви |

Для set-групп порядок выбранных альтернатив в CLI не обязан совпадать с
порядком в паттерне. Эти группы остаются символическими узлами графа и
разбираются matcher-слоем во время выполнения.

### Контекстный `*`

`*` является оператором множества только сразу после закрывающей скобки
группы. Пробелы перед ним значения не имеют:

```text
{ create | read } *
[ fast | safe ] *
```

В позиции, где parser ожидает новый atom, `*` является обычным CLI-литералом:

```text
access-operation { { create | read } * | * }
                                             └─ литерал "*"
```

Таким образом, в паттерне выше внутренний `*` после `}` меняет режим
внутренней группы, а последний `*` задаёт буквальный токен CLI.

### Повторение `&<min-max>`

Оператор повторяет только параметр или группу:

```text
community STRING<3-11> &<1-200>
path { left | right } &<1-3>
```

Ограничения:

- границы — неотрицательные десятичные целые числа;
- `maximum` должен быть не меньше `minimum`;
- между `&` и `<` пробел недопустим;
- пробелы внутри угловых скобок допустимы;
- повторять literal нельзя;
- второй последовательный repeat не поддерживается.

Примеры:

```text
STRING<1-10>&<0-3>       # допустимо
STRING<1-10>&< 0 - 3 >   # допустимо
literal&<1-2>            # ошибка
STRING<1-10>&<3-2>       # ошибка границ
cmd & <1-2>              # malformed repeat
```

### `|` в корне и внутри группы

На верхнем уровне `|` — буквальный CLI-токен:

```text
display | include STRING<1-20>
```

Внутри `{ ... }` и `[ ... ]` он всегда разделяет альтернативы. Пустые
альтернативы запрещены:

```text
[ left | ]   # ошибка
```

### Параметры

`PatternLexer` не содержит списка типов параметров. Переданный
`ParameterRecognizer` пробует распознать объявление ровно с текущей позиции.
Благодаря этому новый placeholder добавляется через registry, не меняя lexer
и parser.

Runtime policy по умолчанию ожидает корректное распознавание следующих
встроенных написаний:

```text
HEX<min-max>
STRING<min-max>
INTEGER<min-max>
ENUM{value1,value2,...}
PASSWORDEX<min-max>
TEXT<min-max>
YYYY/MM/DD
YYYY-MM-DD
MM-DD
MM-DD-YYYY
YYYY/MM/DD,HH:MM:SS
HH:MM:SS
<hh:mm>
H-H-H
X.X.X.X
X:X::X:X
X:X::X:X/M
```

`TEXT<min-max>` является bounded remainder-параметром. Он может находиться
после keyword, например `description TEXT<1-80>`, и тогда принимает весь
оставшийся текст. Он обязан завершать возможный route и не может повторяться.
Если `TEXT` сопоставляется в позиции `0` — на bare/root route без уже
совпавшего keyword, — matcher разрешает его только для CLI-строк, начинающихся
после отступа с `!`. Это runtime-правило не даёт такой remainder-ветке
поглощать неизвестные команды; frontend при этом разрешает terminal embedded
`TEXT`.

Три IP-placeholder’а являются встроенными точными declarations. Registry
распознаёт `X:X::X:X/M` отдельно от `X:X::X:X` по самому длинному совпадению.
Runtime policy резервирует все три написания: malformed suffix вроде
`X.X.X.X/suffix` или `X:X::X:X/M-extra` не превращается в literals, а даёт
`PatternLanguageError` при компиляции.

## Пример AST

Исходный паттерн:

```text
route [ vpn STRING<1-31> ] { preference INTEGER<1-255> | * }
```

Упрощённое представление AST:

```text
Sequence
├── Literal("route")
├── Group(mode=OPTIONAL_ONE)
│   └── Sequence
│       ├── Literal("vpn")
│       └── Parameter(source="STRING<1-31>")
└── Group(mode=REQUIRED_ONE)
    ├── Sequence
    │   ├── Literal("preference")
    │   └── Parameter(source="INTEGER<1-255>")
    └── Sequence
        └── Literal("*")
```

Каждый узел содержит `SourceSpan(start, end)` — полуоткрытый диапазон
символов `[start, end)` в исходной строке. Пробелы, разделяющие узлы, обычно
не входят в их span.

# `vrp_parser.patterns.tokens`

## `SourceSpan`

```python
@dataclass(frozen=True, slots=True)
class SourceSpan:
    start: int
    end: int
```

Неизменяемый полуоткрытый диапазон в строке паттерна.

- `start` включён;
- `end` не включён;
- допустимый инвариант: `0 <= start <= end`;
- нарушение инварианта в `__post_init__()` вызывает `ValueError`.

Пустой диапазон допустим. Например, токен `END` получает
`SourceSpan(len(source), len(source))`.

### `SourceSpan.covering(first, last)`

Возвращает `SourceSpan(first.start, last.end)`. Метод рассчитан на то, что
`first` расположен не правее `last`. Он не сортирует аргументы и не ищет
`min`/`max`; корректный порядок — ответственность вызывающего кода.

Вход:

- `first: SourceSpan`;
- `last: SourceSpan`.

Результат: новый `SourceSpan`.

Исключение: `ValueError`, если полученная пара границ нарушает инвариант.

## `TokenKind`

`StrEnum`, определяющий виды токенов:

- `PARAMETER` — распознанное registry объявление параметра;
- `LITERAL` — фиксированный CLI-токен;
- `LEFT_BRACE`, `RIGHT_BRACE` — `{`, `}`;
- `LEFT_BRACKET`, `RIGHT_BRACKET` — `[`, `]`;
- `PIPE` — `|`;
- `STAR` — `*`;
- `REPEAT` — полный оператор `&<min-max>`;
- `END` — синтетический конец входа.

Поскольку это `StrEnum`, строковые значения имеют вид `"parameter"`,
`"literal"`, `"left_brace"` и так далее.

## `Token`

```python
@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    text: str
    span: SourceSpan
    parameter: object | None = None
    repeat_bounds: tuple[int, int] | None = None
```

Лексема с исходным текстом, позицией и необязательными данными.

- `parameter` заполнен только у `PARAMETER`;
- `repeat_bounds` заполнен только у `REPEAT`;
- для `END` поле `text` равно пустой строке.

`__post_init__()` проверяет согласованность kind и metadata:

- `PARAMETER` без `parameter` → `ValueError`;
- любой другой kind с `parameter` → `ValueError`;
- `REPEAT` без `repeat_bounds` → `ValueError`;
- любой другой kind с `repeat_bounds` → `ValueError`.

Класс не проверяет, что длина `text` равна длине `span`: эту связь
обеспечивает lexer.

# `vrp_parser.patterns.lexer`

## `RecognizedParameter`

Protocol минимального результата от registry-like распознавателя.

### `end`

Read-only property типа `int`. Это исключительная позиция конца объявления
в исходном паттерне.

Lexer дополнительно ищет у результата атрибут `declaration`:

- если атрибут существует, в `Token.parameter` сохраняется его значение;
- иначе сохраняется сам объект результата.

Это позволяет использовать как production `ParameterDeclaration`, так и
небольшие адаптеры пользовательских recognizer-ов.

## `ParameterRecognizer`

Protocol зависимости `PatternLexer` и `PatternParser`.

### `recognize(source, position)`

Вход:

- `source: str` — полный исходный паттерн;
- `position: int` — точная позиция предполагаемого начала.

Результат:

- объект, совместимый с `RecognizedParameter`, если объявление начинается
  ровно в `position`;
- `None`, если с этой позиции параметра нет.

Recognizer не должен пропускать символы слева и обязан вернуть
`end > position`.

## `PatternLexer`

Преобразует строку паттерна в токены. Он знает структурные символы языка, но
не знает конкретные типы параметров.

### `PatternLexer.__init__(parameter_recognizer)`

Принимает объект, реализующий `ParameterRecognizer`. Объект сохраняется и
используется при каждом вызове `tokenize()`.

### `PatternLexer.tokenize(source)`

Вход: одна полная строка паттерна `source: str`.

Результат: `tuple[Token, ...]`, всегда заканчивающийся токеном `END`.

Порядок распознавания в каждой непустой позиции:

1. parameter через registry;
2. repeat по регулярному выражению;
3. один структурный символ `{ } [ ] | *`;
4. literal до пробела, структурного символа или `&`.

Распознавание параметра выполняется первым, поэтому внутренние `{`, `}`,
`,` и другие знаки в `ENUM{...}` не превращаются в структурные токены.

Пробелы не создают токенов. Все другие непробельные последовательности
становятся `LITERAL`.

Исключения:

- `TypeError`, если `source` не строка;
- `PatternLanguageError("malformed repeat operator", ...)`, если встречен
  `&`, с которого не начинается корректный repeat;
- `RuntimeError`, если parameter recognizer нарушил контракт позиции.

### `PatternLexer._literal_end(source, position)`

Class method, который ищет конец literal. Движется вправо до:

- whitespace;
- одного из `{ } [ ] | *`;
- символа `&`;
- конца строки.

Возвращает исключительную позицию конца. Если стартовый символ — `&`,
возвращает исходную `position`; `tokenize()` интерпретирует это как
malformed repeat.

### `PatternLexer._validate_parameter_end(source, start, end)`

Проверяет ответ внешнего recognizer:

- `end <= start` → `RuntimeError("parameter recognizer did not advance")`;
- `end > len(source)` → `RuntimeError("parameter recognizer advanced beyond the source")`.

Метод ничего не возвращает. Проверка границы между объявлением и следующим
элементом должна находиться в самом declaration recognizer.

Внутренние константы:

- `_SYMBOLS` отображает одиночные структурные символы в `TokenKind`;
- `_REPEAT` распознаёт десятичные границы и необязательные пробелы внутри
  `&<...>`.

# `vrp_parser.patterns.ast`

Все AST-значения — frozen dataclass со slots. После построения их нельзя
изменить.

## `GroupMode`

`StrEnum` с четырьмя режимами:

- `OPTIONAL_ONE = "optional_one"`;
- `REQUIRED_ONE = "required_one"`;
- `OPTIONAL_SET = "optional_set"`;
- `REQUIRED_SET = "required_set"`.

Он одновременно описывает обязательность группы, количество выбранных
альтернатив и наличие/отсутствие порядка.

## `Sequence`

```python
@dataclass(frozen=True, slots=True)
class Sequence:
    items: tuple[Node, ...]
    span: SourceSpan
```

Упорядоченная слева направо последовательность AST-узлов. Корень любого
успешно разобранного паттерна — `Sequence`. Внутри группы каждая
альтернатива также представлена отдельной `Sequence`.

Корневая пустая последовательность запрещена parser-ом. На промежуточном
этапе parser может создать пустую последовательность, чтобы выдать точную
ошибку пустой альтернативы.

## `Literal`

```python
@dataclass(frozen=True, slots=True)
class Literal:
    value: str
    span: SourceSpan
```

Фиксированный CLI-токен. `value` хранит исходный регистр. При построении
графа ASCII-регистр исключается из семантического key, поэтому ключевые
слова сопоставляются без учёта ASCII-регистра.

## `Parameter`

```python
@dataclass(frozen=True, slots=True)
class Parameter:
    declaration: object
    source: str
    span: SourceSpan
```

Placeholder параметра:

- `declaration` — metadata, возвращённые recognizer-ом;
- `source` — точный фрагмент исходного паттерна;
- `span` — позиция этого фрагмента.

Нейтральный frontend допускает `object`, но production
`RuntimePatternPolicy` и `GraphStepFactory` требуют экземпляр
`ParameterDeclaration`. Иначе они выбрасывают `TypeError`.

## `Group`

```python
@dataclass(frozen=True, slots=True)
class Group:
    alternatives: tuple[Sequence, ...]
    mode: GroupMode
    span: SourceSpan
```

Группа альтернатив. Parser гарантирует как минимум одну непустую
альтернативу. `span` включает обе скобки и, для set-группы, завершающий `*`.

## `Repeat`

```python
@dataclass(frozen=True, slots=True)
class Repeat:
    atom: Parameter | Group
    minimum: int
    maximum: int
    span: SourceSpan
```

Ограниченное повторение параметра или группы. `span` покрывает atom и
оператор `&<min-max>`. Parser гарантирует:

- atom имеет тип `Parameter` или `Group`;
- `minimum <= maximum`.

## `Node`

Type alias:

```python
type Node = Literal | Parameter | Group | Repeat
```

`Sequence` намеренно не входит в `Node`: она контейнер корня или
альтернативы, а не отдельное ребро команды.

# `vrp_parser.patterns.errors`

## `PatternLanguageError`

Наследник `ValueError`, представляющий одну ошибку языка паттернов.

### `PatternLanguageError.__init__(message, source, span)`

Сохраняет публичные атрибуты:

- `message: str` — описание без координат;
- `source: str` — полный исходный паттерн;
- `span: SourceSpan` — точный проблемный диапазон.

Стандартное строковое представление исключения:

```text
<message> at characters <start>:<end>
```

Его выбрасывают lexer, parser и runtime policy.

# `vrp_parser.patterns.parser`

## `PatternParser`

Recursive-descent parser, преобразующий поток токенов в immutable AST.

Экземпляр хранит текущие `_source`, `_tokens` и `_position`, поэтому один
объект не предназначен для одновременных вызовов `parse()` из нескольких
потоков. `PatternCompiler` использует его последовательно.

### `PatternParser.__init__(parameter_recognizer)`

Создаёт внутренний `PatternLexer` с переданным recognizer-ом и
инициализирует пустое состояние разбора.

### `PatternParser.parse(source)`

Вход: полная строка паттерна.

Алгоритм:

1. вызвать lexer;
2. сбросить позицию на первый токен;
3. разобрать корневую последовательность до `END`;
4. потребовать `END`;
5. запретить пустой корень.

На корневом уровне `pipe_is_literal=True`, поэтому `|` превращается в
`Literal("|")`.

Результат: корневой `Sequence`.

Исключения:

- исключения `PatternLexer.tokenize()`;
- `PatternLanguageError` при ошибке грамматики.

### `PatternParser._parse_sequence(*, stop, pipe_is_literal)`

Собирает последовательность до одного из токенов `stop`. Для каждого
элемента сначала вызывает `_parse_atom()`, затем `_parse_repeat()`.

Если `pipe_is_literal=False`, дополнительно завершает альтернативу перед
`PIPE`, не поглощая его.

Вход:

- `stop: frozenset[TokenKind]`;
- `pipe_is_literal: bool`.

Результат: `Sequence`, в том числе временно пустая. Её span начинается с
текущего токена и заканчивается концом последнего элемента.

### `PatternParser._parse_atom(*, pipe_is_literal)`

Поглощает один токен и создаёт `Node`:

- `PARAMETER` → `Parameter`;
- `LITERAL` → `Literal`;
- `STAR` → `Literal("*")`;
- корневой `PIPE` → `Literal("|")`;
- открывающая скобка → `_parse_group()`.

Вызывает `_fail()` для:

- неожиданной закрывающей скобки;
- repeat без предшествующего atom;
- `PIPE` там, где он не может быть literal;
- `END` или другого неожиданного токена.

### `PatternParser._parse_group(opening)`

Разбирает required `{...}` или optional `[...]` группу.

Алгоритм:

1. определить ожидаемую закрывающую скобку;
2. разобрать одну или несколько непустых альтернатив;
3. поглотить закрывающую скобку;
4. если следующий токен `STAR`, поглотить его и выбрать set-режим;
5. иначе выбрать one-режим.

Результат: `Group`. Его `span` заканчивается после `*` для set-группы или
после закрывающей скобки для one-группы.

Исключения: `PatternLanguageError` для пустой альтернативы, отсутствующей
или неправильной закрывающей скобки и других вложенных ошибок.

### `PatternParser._parse_repeat(atom)`

Если текущий токен не `REPEAT`, возвращает исходный `atom` без изменений.

Если repeat присутствует:

1. извлекает `(minimum, maximum)`;
2. проверяет `maximum >= minimum`;
3. проверяет, что atom — `Parameter` или `Group`;
4. возвращает `Repeat`.

`Literal` перед repeat приводит к `PatternLanguageError`. Метод разбирает
не более одного repeat для atom.

### `PatternParser._current`

Read-only private property. Возвращает `Token` по текущему `_position`.
Parser всегда поддерживает в конце `_tokens` элемент `END`, поэтому при
корректной внутренней работе индекс остаётся допустимым.

### `PatternParser._advance()`

Возвращает текущий токен и увеличивает позицию, если это не `END`. На `END`
позиция не меняется, что защищает от выхода за tuple.

### `PatternParser._expect(kind)`

Проверяет kind текущего токена.

- При совпадении поглощает и возвращает токен через `_advance()`.
- При несовпадении вызывает `_fail()` с ожидаемым kind, фактическим text и
  span фактического токена.

### `PatternParser._fail(message, span)`

Всегда выбрасывает `PatternLanguageError`, добавляя сохранённый `_source`.
Return type — `NoReturn`.

# `vrp_parser.patterns.policy`

## `RuntimePatternPolicy`

Слой проектных ограничений поверх нейтральной грамматики. Parser сам по себе
не может отличить неизвестный placeholder от обычного literal. Policy
предотвращает тихое превращение опечатки вроде `STRING<1-x>` или malformed
точного placeholder’а `X.X.X.X/suffix` в literal.

Внутренние списки:

- `_DECLARATION_PREFIXES` — известные начала параметризованных объявлений;
- `_EXACT_PLACEHOLDERS` — встроенные объявления с фиксированным написанием,
  включая IPv4 address, IPv6 address и IPv6 prefix.

### `RuntimePatternPolicy.validate(ast, source)`

Вход:

- `ast: Sequence` — уже успешно построенный AST;
- `source: str` — та же исходная строка.

Алгоритм:

1. рекурсивно собрать все `Parameter`;
2. найти каждое появление известного declaration prefix в исходной строке;
3. убедиться, что его диапазон покрыт span реального `Parameter`;
4. аналогично проверить exact placeholders;
5. проверить, что каждый `TEXT` является последним элементом возможного
   route и не находится под repeat.

Ничего не возвращает.

Исключения:

- `PatternLanguageError`, если известное написание осталось literal,
  malformed, а также если `TEXT` не завершает route либо повторяется;
- `TypeError`, если production AST содержит неизвестный тип declaration.

IP-placeholder считается корректным только тогда, когда зарегистрированный
exact recognizer действительно превратил его диапазон в `Parameter`. Поэтому
проверка policy также ловит недопустимые суффиксы после зарезервированного
написания.

### `RuntimePatternPolicy._claimed(parameters, start, end)`

Возвращает `True`, если существует `Parameter`, чей span полностью покрывает
диапазон `[start, end)`.

Это проверка владения исходным фрагментом. Простого пересечения диапазонов
недостаточно.

### `RuntimePatternPolicy._validate_text_sequence(sequence, source, *, followed)`

Рекурсивно проверяет расположение bounded remainder-параметров
`TEXT<min-max>`.

- `TEXT` допустим как последний элемент корневой последовательности:
  `TEXT<1-4096>`;
- `TEXT` допустим после keyword: `description TEXT<1-80>`;
- если после текущей последовательности существует продолжение,
  `followed=True`;
- `TEXT` с последующим literal, parameter или внешним продолжением group
  отклоняется с `PatternLanguageError`;
- для обычной `Group` метод проверяет каждую alternative с учётом того,
  следует ли что-либо за самой группой.

Ограничение связано с reader-семантикой: `TEXT` поглощает весь остаток строки,
поэтому никакой следующий элемент сопоставить уже невозможно.

### `RuntimePatternPolicy._validate_repeated_text(repeat, source)`

Запрещает непосредственно повторяемый `TEXT<min-max>`, поскольку первый
remainder уже поглощает весь доступный ввод. Для повторяемой группы рекурсивно
проверяет каждую alternative как имеющую продолжение: после одного повторения
потенциально должен начаться следующий.

### `RuntimePatternPolicy._is_text(parameter)`

Возвращает `True`, когда `parameter.declaration.type_id == "text"`. Для
проверки production-инварианта использует `_declaration()`.

### `RuntimePatternPolicy._parameters(sequence)`

Generator, рекурсивно обходящий:

- непосредственные `Parameter`;
- все alternatives у `Group`;
- `Repeat.atom`, если это `Parameter`;
- все alternatives повторяемой `Group`.

`Literal` пропускается. Результат — `Iterator[Parameter]`.

### `RuntimePatternPolicy._declaration(parameter)`

Проверяет production-инвариант: `parameter.declaration` должен быть
`ParameterDeclaration`.

Результат: типизированный `ParameterDeclaration`.

Исключение: `TypeError("parameter AST contains an unknown declaration")`.

### `RuntimePatternPolicy._fail(message, source, start, end)`

Создаёт `SourceSpan(start, end)` и выбрасывает `PatternLanguageError`.
Нормально не возвращается.

# `vrp_parser.patterns.__init__`

Package facade экспортирует:

```text
Group, GroupMode, Literal, Node, Parameter,
ParameterRecognizer, PatternLanguageError, PatternLexer, PatternParser,
RecognizedParameter, Repeat, RuntimePatternPolicy, Sequence,
SourceSpan, Token, TokenKind
```

Это стабильная точка импорта frontend-сущностей внутри проекта:

```python
from vrp_parser.patterns import PatternParser, Sequence
```

# `vrp_parser.errors`

## `PatternDocumentError`

Наследник `ValueError`. Сигнализирует о неверной форме входного
JSON-совместимого документа, а не о синтаксисе отдельного паттерна.

Примеры причин:

- отсутствует `commands`;
- `commands` не является массивом строк;
- список пуст;
- один из элементов пуст или не строка.

Собственных полей и методов класс не добавляет.

## `PatternIssue`

```python
@dataclass(frozen=True, slots=True)
class PatternIssue:
    pattern_index: int
    pattern: str
    message: str
    span: SourceSpan
```

Одна нормализованная ошибка компиляции:

- `pattern_index` — исходная позиция в `commands`;
- `pattern` — полная строка;
- `message` — сообщение исходного исключения;
- `span` — проблемный диапазон.

Объект immutable. Дополнительных проверок значений нет: корректность индекса
и span обеспечивает compiler.

## `PatternCompilationError`

Наследник `ValueError`, содержащий все ошибки одного прохода компиляции.

### `PatternCompilationError.__init__(issues)`

Вход: непустой `tuple[PatternIssue, ...]`.

Сохраняет tuple в публичном атрибуте `issues`. Порядок соответствует порядку
паттернов во входном `commands`.

Текст исключения показывает первую проблему:

```text
pattern #7: expected right_brace, found ''
```

При нескольких проблемах добавляется суффикс:

```text
 (+2 more)
```

Если передан пустой tuple, constructor выбрасывает обычный `ValueError`,
поскольку exception без issues нарушает инвариант.

# `vrp_parser.compiler`

## `PatternCompiler`

Application service, который преобразует коллекцию исходных строк в один
`CommandGraph`. Он отвечает за полный сбор ошибок и source identity, но
делегирует grammar, policy и построение графа отдельным объектам.

### `PatternCompiler.__init__(parameter_types, graph_builder=None, pattern_policy=None)`

Зависимости:

- `parameter_types: ParameterTypeRegistry` — registry declaration
  recognizer-ов;
- `graph_builder: CommandGraphBuilder | None` — необязательная замена
  builder; по умолчанию создаётся `CommandGraphBuilder()`;
- `pattern_policy: RuntimePatternPolicy | None` — необязательная policy; по
  умолчанию создаётся `RuntimePatternPolicy()`.

Constructor создаёт один `PatternParser(parameter_types)`. Registry должен
оставаться согласованным на протяжении компиляции.

### `PatternCompiler.compile(commands)`

Вход: `commands: tuple[str, ...]`, уже проверенный document-слоем.

Для каждого элемента в исходном порядке:

1. построить AST;
2. проверить runtime policy;
3. при ошибке добавить `PatternIssue` и перейти к следующей строке;
4. при успехе создать `PatternSource`.

Compiler перехватывает:

- `PatternLanguageError`;
- `ParameterDeclarationError`;
- `ParameterRegistryError`.

Он не прекращает обработку на первой ошибке. Если после прохода есть хотя бы
один issue, выбрасывается `PatternCompilationError(tuple(issues))` и граф не
возвращается. Неожиданные programming errors не подавляются.

При отсутствии ошибок вызывается:

```python
self._graph_builder.build(tuple(patterns))
```

Результат: immutable `CommandGraph`.

### `PatternCompiler._pattern_id(original, occurrence)`

Static method формирования стабильного source ID:

```text
pattern:<20 первых hex символов sha256 UTF-8 строки>:<occurrence>
```

Например:

```text
pattern:7d89...e410:0
```

`occurrence` — номер точного дубликата этой строки среди успешно
обработанных источников, начиная с нуля. Поэтому:

- вставка другого паттерна не меняет ID;
- изменение регистра или пробелов меняет hash;
- точные дубликаты получают разные ID;
- `pattern_index` при перестановке документа меняется, а content part ID —
  нет.

Результат: `str`.

### `PatternCompiler._issue(index, pattern, error)`

Static adapter произвольного ожидаемого frontend/registry исключения к
`PatternIssue`.

Выбор span:

1. использовать `error.span`, если это `SourceSpan`;
2. иначе взять integer-атрибуты `error.start` и `error.end`;
3. невалидный/missing `start` заменить на `0`;
4. невалидный/missing `end` заменить на `len(pattern)`.

Сообщение берётся из `error.message`, а при его отсутствии — из
`str(error)`.

Результат: `PatternIssue`.

# Линеаризация маршрутов

Обычные choice-группы удобно раскрыть при компиляции: тогда их literal и
parameter prefixes объединяются с префиксами других паттернов. Полное
раскрытие set-групп или repeats было бы факториальным/экспоненциальным,
поэтому они остаются единым символическим шагом.

Пример:

```text
show { interface | version }
```

даёт два `LinearRoute`:

```text
("show", "interface")
("show", "version")
```

Паттерн:

```text
select { red | green | blue } *
```

даёт один маршрут:

```text
("select", Group(REQUIRED_SET, ...))
```

# `vrp_parser.graph.routes`

## `LinearRoute`

```python
@dataclass(frozen=True, slots=True)
class LinearRoute:
    steps: tuple[Node, ...]
    trace: tuple[VariationStep, ...] = ()
```

Одна линейная вариация исходного AST:

- `steps` — последовательность будущих рёбер;
- `trace` — статически известные решения choice/optional.

`VariationStep` импортируется из result model. Для этого слоя используются:

- `kind="choice"` с `selected=(alternative_index,)`;
- `kind="optional"` с пустым `selected` при пропуске;
- `kind="optional"` с индексом при выборе ветви.

## `RouteLimitExceeded`

Внутренний `RuntimeError`. Сигнализирует, что раскрытие одного паттерна
превысило допустимое количество вариантов.

Это не пользовательская ошибка компиляции: публичный `expand()` ловит её и
возвращает исходную последовательность одним символическим маршрутом.

## `RouteExpander`

Контролируемо раскрывает `REQUIRED_ONE` и `OPTIONAL_ONE`, сохраняя сложные
конструкции символическими.

### `RouteExpander.__init__(maximum_routes=512)`

`maximum_routes` — максимальное число линейных вариантов одного паттерна.

- Значение должно быть положительным.
- `maximum_routes < 1` приводит к `ValueError`.

Лимит защищает и отдельную группу, и декартово произведение нескольких
групп в последовательности.

### `RouteExpander.expand(sequence)`

Вход: корневая `Sequence`.

Нормальный результат: tuple раскрытых `LinearRoute`.

Если на любом уровне возникает `RouteLimitExceeded`, метод полностью
отказывается от частичного раскрытия и возвращает:

```python
(LinearRoute(sequence.items),)
```

Это важно: fallback сохраняет корректность всего исходного паттерна и не
смешивает частично статическую provenance с символической.

### `RouteExpander._sequence(sequence, *, path)`

Начинает с одного пустого маршрута. Для каждого узла:

1. получить его маршруты через `_node()`;
2. умножить накопленные маршруты на варианты узла через `_product()`.

`path` задаёт адрес AST для trace. Корень начинается с `"root"`, а индекс
элемента добавляется через точку: `"root.0"`, `"root.1"`.

Результат: `tuple[LinearRoute, ...]`.

### `RouteExpander._node(node, *, path)`

Поведение по типу узла:

- literal, parameter, repeat → один маршрут с самим узлом;
- `OPTIONAL_SET`, `REQUIRED_SET` → один маршрут с символической группой;
- `REQUIRED_ONE` → маршруты всех alternatives;
- `OPTIONAL_ONE` → сначала пустой маршрут пропуска, затем маршруты всех
  alternatives.

Для вложенной alternative её индекс добавляется к path до рекурсивного
разбора. После trace вложенной ветви добавляется `VariationStep` выбора
текущей группы.

Пример для второго элемента корня:

```text
[ brief | detail ]
```

Trace пропуска:

```python
VariationStep(kind="optional", path="root.1", selected=())
```

Trace выбора `detail`:

```python
VariationStep(kind="optional", path="root.1", selected=(1,))
```

### `RouteExpander._product(left, right)`

Строит декартово произведение маршрутов:

- steps объединяются конкатенацией;
- traces объединяются конкатенацией;
- порядок детерминирован: сначала порядок `left`, внутри него порядок
  `right`.

До materialization проверяет `len(left) * len(right)` через `_check_limit()`.

Результат: `tuple[LinearRoute, ...]`.

### `RouteExpander._check_limit(size)`

Если `size > self._maximum_routes`, выбрасывает `RouteLimitExceeded`.
Иначе ничего не возвращает.

# Семантические шаги

Для объединения префиксов нельзя сравнивать AST dataclass напрямую:
`SourceSpan` и регистр literal относятся к исходной записи, а не к смыслу
шага. `GraphStepFactory` создаёт отдельный hashable key без spans.

# `vrp_parser.graph.steps`

## `GraphStepFactory`

Преобразует AST node в `GraphStep(expression, key)`.

### `GraphStepFactory.create(expression)`

Вход: один `Node`.

Результат:

```python
GraphStep(
    expression=expression,
    key=self._node_key(expression),
)
```

`expression` сохраняет читаемый AST первого встретившегося источника; `key`
используется для merge.

### `GraphStepFactory._node_key(node)`

Строит рекурсивный tuple:

```python
Literal:
("literal", ascii_lower(value))

Parameter:
(
    "parameter",
    declaration.type_id,
    declaration.source,
    declaration.minimum,
    declaration.maximum,
    declaration.choices,
    declaration.metadata,
)

Group:
(
    "group",
    mode.value,
    tuple(sequence_key для каждой alternative),
)

Repeat:
(
    "repeat",
    key повторяемого atom,
    minimum,
    maximum,
)
```

Следствия:

- spans не влияют на merge;
- ASCII-регистр literal не влияет на merge;
- порядок alternatives группы влияет;
- режим группы и repeat bounds влияют;
- точный `declaration.source` входит в key;
- metadata должна следовать типизированному контракту
  `tuple[tuple[str, str], ...]` и состоять из hashable значений;
  `ParameterDeclaration` отдельно не проверяет hashability во время runtime.

Неизвестный тип узла приводит к `TypeError`.

### `GraphStepFactory._sequence_key(sequence)`

Возвращает tuple ключей всех элементов `Sequence` в исходном порядке.
Используется рекурсивно для alternatives группы.

### `GraphStepFactory._declaration(node)`

Проверяет, что `Parameter.declaration` — production
`ParameterDeclaration`, и возвращает его.

Исключение: `TypeError("parameter AST contains an unknown declaration")`.

# Модель общего графа

Граф является trie-подобной структурой с общими префиксами. В отличие от
обычного trie, каждое ребро хранит множество `route_ids`, которым разрешено
его проходить.

Это решает проблему ложного crossover:

```text
command one left
command two right
```

После merge общие части могут находиться рядом в одном графе, но маршрут
первого паттерна не имеет права закончиться через ребро второго. Runtime
matcher переносит множество активных route IDs и пересекает его с
`edge.route_ids` на каждом шаге. В конце принимаются только IDs из
`node.accepting_routes`.

# `vrp_parser.graph.model`

## `ascii_lower(value)`

Module-level функция case folding для CLI-keywords.

Вход: `value: str`.

Алгоритм: `str.translate()` заменяет только ASCII `A-Z` на `a-z`.
Non-ASCII символы не меняются.

Результат: `str`.

Это намеренно уже, чем `str.lower()` или `str.casefold()`: поведение
сетевого CLI для ASCII keywords не зависит от Unicode case rules.

## `PatternSource`

```python
@dataclass(frozen=True, slots=True)
class PatternSource:
    pattern_id: str
    index: int
    original: str
    ast: Sequence
```

Одна запись из `commands` после успешного frontend:

- `pattern_id` — content-based ID с номером дубликата;
- `index` — позиция в исходном JSON-массиве;
- `original` — исходная строка без нормализации;
- `ast` — immutable корень.

## `GraphStep`

```python
@dataclass(frozen=True, slots=True)
class GraphStep:
    expression: Node
    key: tuple[object, ...]
```

Семантическое выражение ребра:

- `expression` нужен runtime matcher;
- `key` нужен builder для объединения эквивалентных шагов.

## `RouteSource`

```python
@dataclass(frozen=True, slots=True)
class RouteSource:
    route_id: int
    pattern: PatternSource
    static_trace: tuple[VariationStep, ...]
```

Provenance одного линейного маршрута:

- глобальный integer `route_id`;
- ссылка на полный исходный `PatternSource`;
- решения, уже принятые `RouteExpander`.

Даже точные дубликаты паттерна имеют разные `PatternSource` и маршруты.

## `CommandEdge`

```python
@dataclass(frozen=True, slots=True)
class CommandEdge:
    step: GraphStep
    target: CommandNode
    route_ids: frozenset[int]
```

Ориентированное ребро:

- `step` описывает, что нужно распознать;
- `target` — следующий узел;
- `route_ids` — маршруты-владельцы этого перехода.

Пустое `route_ids` builder не создаёт.

## `CommandNode`

```python
@dataclass(frozen=True, slots=True)
class CommandNode:
    literal_edges: Mapping[str, CommandEdge]
    expression_edges: tuple[CommandEdge, ...]
    accepting_routes: frozenset[int]
```

Один узел общего префиксного графа:

- `literal_edges` — быстрый индекс по `ascii_lower(keyword)`;
- `expression_edges` — параметры, группы и repeats;
- `accepting_routes` — маршруты, которые могут завершиться именно здесь.

Literal вынесены в отдельный mapping, чтобы runtime lookup не перебирал все
выражения.

### `CommandNode.create(literal_edges, expression_edges, accepting_routes)`

Class method защитного construction.

Копирует входной `dict` и оборачивает его в `MappingProxyType`.
Tuple и frozenset уже immutable.

Результат: `CommandNode`. Последующая мутация исходного dict не меняет узел.

## `CommandGraph`

```python
@dataclass(frozen=True, slots=True)
class CommandGraph:
    root: CommandNode
    patterns: tuple[PatternSource, ...]
    routes: Mapping[int, RouteSource]
```

Завершённый артефакт компиляции:

- `root` — старт matching;
- `patterns` — все источники в JSON-порядке;
- `routes` — provenance по `route_id`.

### `CommandGraph.create(root, patterns, routes)`

Копирует mutable `routes: dict[int, RouteSource]` и защищает
`MappingProxyType`.

Результат: `CommandGraph`.

# `vrp_parser.graph.builder`

## `_DraftEdge`

Внутренний mutable dataclass:

```python
@dataclass(slots=True)
class _DraftEdge:
    step: GraphStep
    target: _DraftNode
    route_ids: set[int]
```

Используется только на этапе build. `route_ids` пополняется при вставке
каждого маршрута.

## `_DraftNode`

Внутренний mutable dataclass:

```python
@dataclass(slots=True)
class _DraftNode:
    edges: dict[tuple[object, ...], _DraftEdge]
    accepting_routes: set[int]
```

`edges` индексируются семантическим `GraphStep.key`. Поэтому одинаковые
шаги разных маршрутов физически используют одно draft-ребро.

## `CommandGraphBuilder`

Строит shared-prefix graph и затем рекурсивно замораживает его.

### `CommandGraphBuilder.__init__(route_expander=None, step_factory=None)`

Необязательные зависимости:

- `RouteExpander`, по умолчанию новый экземпляр с лимитом 512;
- `GraphStepFactory`, по умолчанию новый экземпляр.

Dependency injection позволяет независимо тестировать или заменять стратегию
линеаризации/ключей.

### `CommandGraphBuilder.build(patterns)`

Вход: `tuple[PatternSource, ...]`.

Алгоритм:

1. создать пустой `_DraftNode` root;
2. для каждого pattern в исходном порядке вызвать `expand(pattern.ast)`;
3. каждому полученному route присвоить следующий глобальный integer ID;
4. создать `RouteSource` с исходным pattern и static trace;
5. вставить steps в draft graph через `_insert()`;
6. рекурсивно заморозить root;
7. вернуть `CommandGraph.create(...)`.

Нумерация route начинается с нуля и зависит от порядка patterns и порядка
линеаризованных вариантов.

Результат: immutable `CommandGraph`. Пустой tuple технически создаёт пустой
граф, хотя public document layer не допускает пустой `commands`.

### `CommandGraphBuilder._insert(root, expressions, route_id)`

Для каждого expression:

1. получить `GraphStep`;
2. найти draft edge по `step.key`;
3. создать edge и target, если ключ встречен впервые;
4. добавить `route_id` во множество владельцев;
5. перейти в target.

После последнего выражения route ID добавляется в
`node.accepting_routes`.

Метод мутирует только draft-структуру и ничего не возвращает.

### `CommandGraphBuilder._freeze(draft)`

Рекурсивно преобразует mutable draft tree в `CommandNode`.

Порядок:

1. отсортировать semantic keys по `repr` для детерминированного результата;
2. заморозить target каждого edge;
3. заменить `set[int]` на `frozenset[int]`;
4. literal edge положить в индекс по `ascii_lower(value)`;
5. остальные edge добавить в ordered tuple `expression_edges`;
6. вызвать `CommandNode.create()`.

Результат: immutable `CommandNode`.

Builder предполагает ациклическую draft-структуру: каждый insert движется
только к следующему уровню последовательности.

# `vrp_parser.graph.__init__`

Package facade экспортирует production graph API:

```text
CommandEdge
CommandGraph
CommandGraphBuilder
CommandNode
GraphStep
PatternSource
RouteSource
```

`LinearRoute`, `RouteExpander`, `RouteLimitExceeded`, `GraphStepFactory` и
draft-типы остаются внутренними деталями package и импортируются из их
модулей только там, где нужна настройка implementation.

# Алгоритм provenance от JSON до результата

Provenance позволяет вернуть исходный паттерн и конкретную variation даже
после агрессивного merge общих префиксов.

## 1. Identity источника

Для каждой валидной строки создаётся `PatternSource`:

```text
pattern_id = sha256(original) + duplicate occurrence
index      = позиция в commands
original   = исходный текст
ast        = parsed tree
```

Нормализация literal не изменяет `original`.

## 2. Identity маршрута

Каждый `LinearRoute` получает отдельный `route_id` и `RouteSource`.
Choice/optional решения, выполненные при compile time, сохраняются в
`static_trace`.

## 3. Владение рёбрами

При merge edge содержит union всех маршрутов, которым принадлежит этот
семантический переход:

```text
edge.route_ids = {route_1, route_7, route_19}
```

У конечного node отдельно хранится `accepting_routes`.

## 4. Runtime-пересечение

Matcher начинает с допустимых маршрутов root и при переходе оставляет только
маршруты, присутствующие у edge. Поэтому нельзя собрать команду из начала
одного паттерна и окончания другого.

## 5. Восстановление исходника

После успешного завершения matcher использует:

- `graph.routes[route_id].pattern.original`;
- `pattern.pattern_id`;
- `pattern.index`;
- `static_trace`;
- динамический trace символических group/repeat/enum.

Из этого строится `PatternMatch` с `original_pattern`, `variation`,
`variation_id` и полным trace.

Если несколько source patterns приняли одну CLI-строку, provenance каждого
сохраняется отдельно. Выбор primary производится по исходному JSON-порядку,
а остальные возвращаются как alternatives.

# Инварианты и границы ответственности

- Lexer отвечает за точные spans и структурные токены.
- Registry отвечает за синтаксис конкретных parameter declarations.
- Parser отвечает только за grammar и cardinality syntax.
- Runtime policy запрещает известным malformed placeholders тихо становиться
  literals; это относится и к точным встроенным IP declarations.
- Compiler собирает все ожидаемые ошибки и не создаёт частичный граф.
- Route expander раскрывает только безопасное число ordinary choices.
- Step factory отделяет семантическое равенство от source location.
- Builder сохраняет ownership каждого route на каждом edge.
- Graph immutable после construction.
- Проверка значения CLI-параметра и формирование runtime ambiguity находятся
  вне описываемого слоя.
