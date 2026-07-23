# Подсистема сопоставления команд (`vrp_parser.matching`)

Этот документ описывает runtime-часть парсера: как уже скомпилированный
`CommandGraph` сопоставляется с одной строкой CLI, как проверяются параметры,
как выбирается наиболее специфичный маршрут и как формируется результат или
ошибка.

Документ относится ко всем production-модулям каталога
`src/vrp_parser/matching`.

## Граница публичного API

Пользовательский код должен разбирать строки через `CommandLineParser`, а весь
файл — через `ConfigurationParser`:

```python
from vrp_parser import CommandLineParser, ConfigurationParser

line_parser = CommandLineParser.from_json_file("data/commands.json")

line_result = line_parser.parse("interface Vlanif100")
report = ConfigurationParser(line_parser).parse(configuration_text)
```

Пакет `vrp_parser.matching` является внутренним слоем между
`CommandLineParser` и публичными dataclass-результатами из
`vrp_parser.results`. На уровне `matching.__init__` экспортируются только:

- `CommandMatcher` — низкоуровневый matcher одной строки без отступа;
- `ResolvedMatch` — внутренний успешный результат matcher.

Остальные сущности имеют открытые имена ради простоты композиции и
unit-тестирования, но не являются стабильным пользовательским API. В частности,
пользователь не должен вручную создавать `WalkState`, `Candidate` или
вызывать `_walk()`.

## Общий поток данных

```text
строка без отступа
        │
        ▼
CommandText ──► обход CommandGraph ──► Candidate[]
                      │
                      ├─ ExpressionMatcher
                      │    ├─ LiteralExpressionMatcher
                      │    ├─ ParameterExpressionMatcher
                      │    ├─ GroupExpressionMatcher
                      │    └─ RepeatExpressionMatcher
                      │
                      └─ MatchDiagnostics
        │
        ▼
удаление дубликатов ──► Pareto frontier ──► MatchResolver
                                                │
                         ┌──────────────────────┴──────────────────────┐
                         ▼                                             ▼
                  ResolvedMatch                                    ParseError
                         │
                         ▼
        CommandLineParser превращает его в ParsedCommand
```

1. Компилятор заранее объединяет префиксы всех паттернов в один
   `CommandGraph`.
2. `CommandMatcher` обходит подходящие рёбра графа. На каждом ребре
   `ExpressionMatcher` возвращает ноль, одно или несколько следующих
   неизменяемых состояний.
3. Полное достижение терминального узла образует `Candidate`. Кандидат может
   содержать как успешно распознанные, так и отклонённые параметры: это нужно,
   чтобы отличить синтаксическую ошибку от ошибки валидации.
4. `MatchResolver` оставляет недоминируемые по специфичности маршруты.
5. Если более специфичный параметр применим по форме, но невалиден по
   ограничению, он может заблокировать менее специфичный валидный маршрут.
6. Все оставшиеся равноправные совпадения сохраняются. Неоднозначность —
   успешный результат со статусом `ambiguous`, а не ошибка.
7. Если полного candidate нет, отдельные diagnostic-компоненты формируют
   подробный английский `ParseError` и при уместном literal-led сценарии
   добавляют до пяти похожих original patterns.

## Общие форматы и соглашения

### Позиции и диапазоны

- `position`, `start`, `end` — индексы Python-строки, то есть позиции в
  Unicode-кодовых точках, а не в байтах UTF-8.
- Диапазоны полуоткрытые: `[start, end)`.
- Matcher получает команду уже без начального отступа.
- `span_offset` прибавляется при создании публичных диапазонов, чтобы вернуть
  координаты относительно исходной строки вместе с отступом.
- Пробелы между токенами пропускаются через `str.isspace()`.

### Неизменяемые коллекции

Состояния и результаты используют `tuple` и `frozenset`. Это позволяет
безопасно разветвлять поиск: одна ветка не меняет состояние другой.

- Методы с результатом `Iterator[WalkState]` лениво выдают все возможные
  продолжения.
- Методы с результатом `tuple[WalkState, ...]` уже полностью вычислили и, как
  правило, дедуплицировали продолжения.
- Порядок tuple стабилен и участвует в выборе первого совпадения.

### Три состояния параметра

`ParameterResult.status` имеет три значения:

- `VALID` — тип применим и значение прошло проверку;
- `INVALID` — тип определённо применим по форме, но значение нарушает его
  ограничения;
- `NOT_APPLICABLE` — строка вообще не похожа на данный тип.

Различие принципиально. Например, число вне диапазона должно привести к ошибке
валидации и не должно незаметно превратиться в `STRING`. Но нечисловой токен
для `INTEGER` имеет статус `NOT_APPLICABLE` и не блокирует подходящий `STRING`.

Та же логика применяется к IP. `192.0.2.999` имеет форму IPv4, поэтому
`IPv4AddressValidator` возвращает `INVALID` и structured route блокирует
generic `STRING`. `router.example.com` лексически не относится к IPv4 и
возвращает `NOT_APPLICABLE`, поэтому может быть принят `STRING`. Для IPv6
address-like token определяется как значение как минимум с двумя двоеточиями;
zone identifiers с `%` считаются применимыми, но невалидными.

### Вектор dispatch

Каждое успешно пройденное выражение добавляет ранг в
`WalkState.dispatch`:

| Выражение/семейство | Ранг |
|---|---:|
| литерал | 0 |
| `ENUM` | 1 |
| структурированный тип (`X.X.X.X`, `X:X::X:X`, даты, MAC) | 2 |
| числовой тип | 3 |
| общий тип, например `STRING` | 4 |
| параметр-остаток | 5 |
| неизвестный зарегистрированному registry тип | 6 |

Чем меньше ранг, тем специфичнее совпадение. Векторы сравниваются по Pareto,
а не лексикографически.

## `state.py`: внутреннее состояние обхода

### `CapturedParameter`

```python
@dataclass(frozen=True, slots=True)
class CapturedParameter:
    declaration: ParameterDeclaration
    token: ParameterToken
    normalized: object | None
```

Значение параметра, успешно прошедшее валидатор.

- `declaration` — описание placeholder из исходного паттерна: `type_id`,
  исходный текст, ограничения и metadata;
- `token` — исходное значение и его диапазон в CLI-строке;
- `normalized` — значение после нормализации validator. Тип намеренно
  `object | None`, потому что подключаемый тип может вернуть собственный
  объект; matcher не требует его глубокой immutable-семантики.

Объект создаётся `ParameterExpressionMatcher` и позже превращается в публичный
`ParameterValue`.

### `RejectedParameter`

```python
@dataclass(frozen=True, slots=True)
class RejectedParameter:
    declaration: ParameterDeclaration
    token: ParameterToken
    result: ParameterResult
```

Параметр, для которого reader прочитал токен, но validator вернул
`INVALID` или `NOT_APPLICABLE`.

#### `applicable`

```python
@property
def applicable(self) -> bool
```

Возвращает `True` только для `ParameterStatus.INVALID`. Свойство означает
«значение имело форму этого типа, но не прошло проверку», а не просто
«проверка завершилась неуспешно». Используется resolver при блокировании общего
валидного маршрута.

### `WalkState`

```python
@dataclass(frozen=True, slots=True)
class WalkState:
    position: int = 0
    parts: tuple[str, ...] = ()
    parameters: tuple[CapturedParameter, ...] = ()
    rejected: tuple[RejectedParameter, ...] = ()
    dispatch: tuple[int, ...] = ()
    parameter_led: bool | None = None
    source_order: tuple[int, ...] = ()
    trace: tuple[VariationStep, ...] = ()
```

Полный снимок одной ветки backtracking-поиска.

- `position` — позиция чтения в `CommandText.value`;
- `parts` — нормализованные части итоговой variation. Литералы записываются в
  ASCII lower case, параметры — текстом declaration, например
  `("interface", "STRING<1-63>")`;
- `parameters` — валидные захваченные параметры;
- `rejected` — прочитанные, но отклонённые параметры;
- `dispatch` — последовательность рангов пройденных атомов;
- `parameter_led` — diagnostic-классификация первого потреблённого atom:
  `True` для применимого parameter, `False` для literal или
  `NOT_APPLICABLE` parameter, `None` до первого потребления;
- `source_order` — решения групп и повторов в порядке внешнего паттерна;
- `trace` — подробная provenance-информация (`choice`, `optional`, `set`,
  `repeat`, `enum`).

Начальное состояние — `WalkState()` с позицией `0` и пустыми tuple.
Изменённые состояния создаются через `dataclasses.replace`; сам объект
неизменяем.

### `source_order_with_parent()`

```python
def source_order_with_parent(
    base: WalkState,
    result: WalkState,
    decision: int,
) -> tuple[int, ...]
```

Вставляет решение внешней конструкции перед решениями, сделанными внутри её
ветки.

- `base` — состояние до входа в группу или repeat;
- `result` — состояние после разбора вложенного выражения;
- `decision` — индекс выбранной альтернативы или количество повторов;
- результат — новый `source_order`.

Алгоритм сохраняет префикс `base.source_order`, затем добавляет `decision`, а
после него — только решения, появившиеся после `base`. Это обеспечивает
стабильный source-order при вложенных группах.

### `Candidate`

```python
@dataclass(frozen=True, slots=True)
class Candidate:
    route_id: int
    state: WalkState
```

Полное совпадение формы команды с одним линейным маршрутом графа.
`route_id` связывает состояние с `RouteSource`, исходным JSON-паттерном и
compile-time trace. Наличие элементов в `state.rejected` отличает невалидный
кандидат от валидного.

## `text.py`: чтение CLI-строки

### `ascii_lower()`

```python
def ascii_lower(value: str) -> str
```

Переводит только `A-Z` в `a-z` через `str.translate`. Не выполняет Unicode
case folding и не меняет остальные символы. Используется для
case-insensitive-сравнения VRP keywords и стабильных variation/signature.

### `CommandToken`

```python
@dataclass(frozen=True, slots=True)
class CommandToken:
    raw: str
    start: int
    end: int
```

Один разделённый пробелами CLI-токен:

- `raw` — текст без окружающих пробелов;
- `start`, `end` — его полуоткрытый диапазон в строке.

### `CommandText`

Обёртка над одной строкой команды.

#### `__init__(value)`

Сохраняет строку в публичном для подсистемы атрибуте `value`. Проверка типа
выполняется выше, в `CommandLineParser`.

#### `skip_space(position)`

```python
def skip_space(self, position: int) -> int
```

Двигается вправо, пока `value[position].isspace()` истинно. Возвращает первую
непробельную позицию либо `len(value)`.

#### `token(position)`

```python
def token(self, position: int) -> CommandToken | None
```

Пропускает пробелы и читает до следующего whitespace. Возвращает
`CommandToken` или `None`, если после позиции токенов нет. Метод не изменяет
внутренний cursor: позицию всегда передаёт вызывающий код.

#### `at_end(position)`

```python
def at_end(self, position: int) -> bool
```

Возвращает `True`, если после пропуска пробелов достигнут конец строки.

## `diagnostics.py`: синтаксические ожидания

### `MatchDiagnostics`

```python
@dataclass(slots=True)
class MatchDiagnostics:
    position: int = 0
    expected: set[str] = field(default_factory=set)
    non_parameter_path_progress: int = -1
    parameter_path_progress: int = -1
```

Единственный намеренно изменяемый накопитель подсистемы. Он общий для веток
обхода и сохраняет ожидания только в самой дальней достигнутой позиции.
Два progress-поля дополнительно показывают, насколько далеко прошли маршруты,
первым потребившие literal/неприменимый parameter либо применимый parameter.
Они нужны только для решения о keyword suggestions и не меняют matching.

#### `record(position, description, *, parameter_led=None)`

- если новая позиция дальше текущей, заменяет весь набор ожиданий;
- если позиция равна текущей, добавляет `description`;
- если позиция меньше, игнорирует запись.
- `parameter_led=True` означает, что первый parameter был применим;
  `False` означает literal либо неприменимый первый parameter;
  `None` — route ещё ничего не потребил;
- соответствующее progress-поле запоминает максимальную достигнутую позицию,
  даже если глобальное `expected` уже относится к другой ветке.

Так ошибка сообщает наиболее полезную точку, а не ранний неудачный маршрут.

#### `allows_keyword_suggestions`

Property возвращает `True`, когда route без применимого первого parameter
продвинулся не меньше parameter-first route. Если применимый parameter-first
route объясняет строку лучше, literal keyword recommendations подавляются,
чтобы не показывать команды из другого пространства распознавания.

#### `elements(offset=0)`

```python
def elements(self, *, offset: int = 0) -> tuple[ExpectedElement, ...]
```

Сортирует текстовые ожидания и возвращает tuple публичных `ExpectedElement`.
К каждой позиции прибавляется `offset`. Дубликаты отсутствуют благодаря
`set`.

#### `_record_path_progress(position, parameter_led)`

Private helper обновляет одно из progress-полей по явной классификации route.
Значение `None` означает, что route ещё ничего не потребил, поэтому такая
запись не участвует в выборе suggestions. `NOT_APPLICABLE` parameter не может
самостоятельно скрыть полезную keyword-рекомендацию.

## `walking.py`: интерфейс рекурсивного обхода

### `ExpressionWalker`

`Protocol`, от которого matchers групп и повторов зависят вместо конкретного
`ExpressionMatcher`.

#### `walk(expression, state, command, diagnostics, *, path)`

Возвращает `Iterator[WalkState]` со всеми состояниями после одного AST-узла.

#### `sequence(expressions, state, command, diagnostics, *, path)`

Возвращает дедуплицированный `tuple[WalkState, ...]` после последовательного
разбора tuple AST-узлов.

`ExpressionMatcher` удовлетворяет этому протоколу структурно; наследование не
требуется.

## `atoms.py`: литералы и параметры

### `DispatchOrder`

Централизованно сопоставляет `ParameterFamily` с рангом специфичности.

#### `rank(declaration, registry)`

```python
def rank(
    self,
    declaration: ParameterDeclaration,
    registry: ParameterTypeRegistry,
) -> int
```

Находит тип через `registry.get(declaration.type_id)` и возвращает ранг из
таблицы dispatch. Если тип отсутствует, возвращает `6`. Метод не знает
конкретных type ID, поэтому новый тип подключается через registry и выбирает
поведение посредством своего `family`.

### `LiteralExpressionMatcher`

#### `match(expression, state, command, diagnostics)`

```python
def match(...) -> Iterator[WalkState]
```

Сопоставляет один `Literal` с одним whitespace-delimited токеном.

- Сравнение регистронезависимо только для ASCII.
- При несовпадении iterator пуст, а diagnostics получает `repr()` ожидаемого
  литерала в позиции начала токена.
- При совпадении выдаётся одно новое состояние:
  - `position = token.end`;
  - в `parts` добавляется lower-case literal;
  - в `dispatch` добавляется `0`.
  - если начало route ещё не классифицировано, `parameter_led=False`.

Параметры, rejected, source order и trace не меняются.

### `ParameterExpressionMatcher`

#### `__init__(parameter_types, dispatch_order=None)`

Сохраняет `ParameterTypeRegistry`. Опциональный `DispatchOrder` позволяет
подменить стратегию в тесте или composition root.

#### `match(expression, state, command, diagnostics, *, path)`

```python
def match(...) -> Iterator[WalkState]
```

Алгоритм:

1. Проверяет, что `expression.declaration` является
   `ParameterDeclaration`; иначе выбрасывает `TypeError`.
2. Применяет `_text_policy_allows()` к declaration и текущей позиции.
3. Просит registry reader прочитать значение с `state.position`.
4. Если reader не нашёл значение, ничего не выдаёт и записывает declaration в
   diagnostics.
5. Передаёт `token.raw` validator через `registry.probe()`.
6. Добавляет семейный ранг в dispatch.
7. Для `VALID` добавляет `CapturedParameter`; для `INVALID` и
   `NOT_APPLICABLE` — `RejectedParameter`.
8. Для первого parameter устанавливает `parameter_led=True`, если status
   применим (`VALID`/`INVALID`), либо `False` для `NOT_APPLICABLE`.
9. В обоих случаях потребляет токен, добавляет исходный текст declaration в
   `parts` и выдаёт ровно одно состояние.

Важно: отклонённый параметр не завершает маршрут немедленно. Ветка должна
дойти до терминала графа, чтобы resolver мог подтвердить совпадение формы всей
команды и вернуть точную validation error.

`path` — стабильный адрес выражения внутри маршрута, например `step:2` или
`step:2.1.0`; он попадает в provenance trace.

#### `_text_policy_allows(declaration, state, command)`

Защищает от catch-all поведения bare/root `TEXT<min-max>`:

- для declaration не типа `text` возвращает `True`;
- для `TEXT`, сопоставляемого не в позиции `0`, возвращает `True`;
- для `TEXT` в позиции `0` возвращает `True` только тогда, когда первый
  непробельный символ команды — `!`.

Guard находится непосредственно в `ParameterExpressionMatcher`, поэтому
применяется к каждому `Parameter` независимо от его вложенности: на обычном
graph edge, внутри symbolic `Group`, `Repeat` или symbolic route, оставленного
после fallback route expansion. При этом `description TEXT<1-80>` разрешён,
поскольку keyword уже продвинул `state.position`.

#### `_trace(declaration, valid, normalized, state, path)`

Добавляет `VariationStep(kind="enum", path=path,
selected=(str(normalized),))` только для валидного параметра семейства
`ENUM`. Для остальных типов и невалидных enum возвращает старый trace без
изменений.

## `deduplication.py`: стабильное удаление дубликатов

### `WalkStateIdentity`

#### `key(state)`

Возвращает hashable tuple, включающий:

- позицию и variation parts;
- declaration, raw и `repr(normalized)` каждого captured-параметра;
- declaration, raw, status и `repr(issue)` каждого rejected-параметра;
- dispatch;
- `parameter_led`;
- source order;
- trace.

Используется `repr()`, потому что пользовательский plugin вправе вернуть
нехешируемый normalized-объект. Практическое требование к plugin: `repr()`
должен быть стабильным в рамках процесса и различать семантически разные
значения.

### `WalkStateSet`

#### `__init__(identity=None)`

Принимает необязательную стратегию `WalkStateIdentity`.

#### `unique(states)`

Проходит `tuple[WalkState, ...]` слева направо, сохраняет первый state для
каждого identity key и возвращает tuple. Порядок первых появлений сохраняется.

### `CandidateSet`

#### `__init__(identity=None)`

Использует переданную или стандартную `WalkStateIdentity`.

#### `unique(candidates)`

Удаляет дубли из `list[Candidate]`, но добавляет `route_id` к ключу state.
Поэтому одинаковые состояния разных исходных маршрутов не склеиваются:
provenance каждого JSON-паттерна сохраняется. Возвращает tuple в исходном
порядке.

## `frontier.py`: Pareto-специфичность

### `DispatchDominance`

#### `dominates(left, right)`

Сравнивает два `tuple[int, ...]` попарно:

- `left` доминирует `right`, если ни в одной общей позиции его ранг не больше;
- хотя бы в одной общей позиции ранг должен быть строго меньше;
- если общей позиции нет, доминирования нет.

Сравнение идёт через `zip(..., strict=False)`. Если длины отличаются,
дополнительные хвостовые элементы не участвуют.

Примеры:

```text
(1,)    dominates (4,)       # ENUM предпочтительнее STRING
(1, 4) dominates (4, 4)
(1, 4) и (4, 1) несравнимы   # реальная ambiguity сохраняется
(3,)    не dominates (3,)    # равенство не является доминированием
()      не dominates (4,)
```

### `CandidateFrontier`

#### `__init__(dominance=None)`

Принимает стратегию сравнения или создаёт `DispatchDominance`.

#### `select(candidates)`

1. Группирует кандидаты по полному dispatch vector.
2. Сравнивает только уникальные векторы, что ускоряет обработку множества
   идентичных паттернов.
3. Сохраняет все buckets, которые не доминируются другим вектором.
4. Возвращает кандидаты в их исходном порядке.

Кандидаты с одинаковым вектором все остаются: это необходимо для статусов
`equivalent` и `ambiguous`.

#### `dominates(left, right)`

Публичный для resolver делегат к настроенной стратегии dominance.

## `alternatives.py`: раннее сокращение ветвей

### `AlternativeOutcome`

```python
@dataclass(frozen=True, slots=True)
class AlternativeOutcome:
    alternative: int
    state: WalkState
```

Результат одной ветки группы: индекс альтернативы и достигнутое состояние.

### `AlternativeStateFrontier`

Нужен для ограничения комбинаторного роста внутри group set и repeat до того,
как найден конец всей команды.

#### `__init__(dominance=None, identity=None)`

Принимает стратегии Pareto-сравнения и идентичности state.

#### `select(outcomes, base)`

Группирует outcomes по конечной `state.position`, обрабатывает каждую позицию
через `_at_position()` и объединяет результаты по возрастанию позиции.

Состояния на разных позициях нельзя сравнивать по специфичности: они потребили
разное количество входа и могут иметь разные продолжения.

#### `_at_position(outcomes, base)`

Для одной конечной позиции:

1. Находит ветки, которые не добавили новый `NOT_APPLICABLE` после `base`.
2. Если такие есть, исключает ветки с новым `NOT_APPLICABLE`; иначе временно
   оставляет все.
3. Сравнивает только новый suffix dispatch, созданный этой альтернативой.
4. Оставляет недоминируемый Pareto frontier.
5. Удаляет одинаковые states, сохраняя первый.
6. Если все варианты содержат новый `NOT_APPLICABLE`, оставляет только один
   представитель. Его достаточно для полезной validation error, а повторение
   таких состояний вызвало бы экспоненциальный рост.

#### `_unique(outcomes)`

Стабильно удаляет одинаковые состояния через `WalkStateIdentity`. Индекс
альтернативы не входит в ключ: если две ветки привели к полностью одинаковому
state, остаётся первая.

#### `_dispatch(state, base)`

Возвращает только часть dispatch, добавленную после входа в альтернативу:
`state.dispatch[len(base.dispatch):]`.

#### `_has_new_not_applicable(state, base)`

Проверяет только новые rejected-параметры и возвращает `True`, если среди них
есть `ParameterStatus.NOT_APPLICABLE`.

## `groups.py`: выбор и unordered set

### `GroupExpressionMatcher`

Поддерживает четыре `GroupMode`:

| Синтаксис паттерна | `GroupMode` | Семантика |
|---|---|---|
| `{ a \| b }` | `REQUIRED_ONE` | ровно одна альтернатива |
| `[ a \| b ]` | `OPTIONAL_ONE` | ноль или одна |
| `{ a \| b } *` | `REQUIRED_SET` | от одной до всех, каждая не более раза, порядок произвольный |
| `[ a \| b ] *` | `OPTIONAL_SET` | от нуля до всех, каждая не более раза, порядок произвольный |

#### `__init__(alternatives=None)`

Принимает `AlternativeStateFrontier` или создаёт стандартный.

#### `match(expression, state, command, diagnostics, *, path, walker)`

Выбирает `_set()` для `OPTIONAL_SET`/`REQUIRED_SET`, иначе `_choice()`.
Лениво выдаёт все допустимые состояния.

#### `_choice(...)`

- Для optional-группы сначала выдаёт ветку пропуска. В source order ей
  соответствует `0`, а trace получает
  `VariationStep(kind="optional", path=path, selected=())`.
- Затем прогоняет sequence каждой альтернативы от одного исходного state.
- Результаты проходят ранний `AlternativeStateFrontier`.
- Внешнее решение вставляется перед вложенными через
  `source_order_with_parent()`.
- Для required choice source-order decision равен индексу альтернативы.
- Для optional choice выбранные альтернативы получают `index + 1`, потому что
  значение `0` уже занято вариантом пропуска.
- Trace получает `kind="choice"` или `"optional"` и
  `selected=(alternative_index,)`.

#### `_set(...)`

Реализован рекурсивный backtracking:

- `minimum = 0` для optional set и `1` для required set;
- `used: frozenset[int]` запрещает выбирать одну альтернативу дважды;
- `order: tuple[int, ...]` хранит фактический порядок выбора;
- как только достигнут minimum, текущее состояние выдаётся с
  `VariationStep(kind="set", selected=order)`;
- затем matcher пробует каждую ещё не использованную альтернативу;
- результаты без продвижения позиции отбрасываются, поэтому nullable-ветка не
  может создать бесконечную рекурсию;
- ранний frontier применяется **отдельно к каждой альтернативе**. Сравнивать
  разные alternative indices на этом этапе нельзя: выбор влияет на множество
  оставшихся веток и иначе потеряется корректная перестановка;
- source order обновляется для каждого выбора, затем поиск продолжается с
  расширенным `used`.

В худшем случае число перестановок set факториально, но запрет повторного
выбора, отбрасывание zero-progress и ранняя дедупликация существенно
ограничивают практический поиск.

##### Локальная функция `visit(current, used, order)`

Вложенный recursive helper метода `_set()`. Принимает текущее `WalkState`,
immutable-множество уже использованных alternative indices и порядок выбора.
Лениво возвращает `Iterator[WalkState]`: сначала допустимый текущий set, затем
состояния всех рекурсивных продолжений с одной новой consuming alternative.

## `repeats.py`: ограниченное повторение

### `RepeatExpressionMatcher`

Обрабатывает AST `Repeat(atom, minimum, maximum)`, возникающий из
`&<min-max>`.

#### `__init__(states=None)`

Принимает `WalkStateSet` для дедупликации frontier после каждого шага.

#### `match(expression, state, command, diagnostics, *, path, walker)`

1. Начальный frontier содержит исходный state.
2. Если `minimum == 0`, сразу выдаёт вариант с нулём повторов.
3. Для `count` от `1` до `maximum` строит следующий frontier через `_next()`.
4. Пустой frontier прекращает цикл: дальнейшие повторы невозможны.
5. При `count >= minimum` выдаёт каждое состояние frontier с provenance
   фактического количества.

Метод возвращает iterator всех допустимых cardinality, а не только
максимального количества. Продолжение паттерна определит, какой вариант
сможет дойти до терминала.

#### `_next(expression, frontier, command, diagnostics, *, path, count, walker)`

Применяет `walker.walk()` к atom для каждого текущего state. Путь конкретного
повтора имеет вид `"{path}.{count - 1}"`. Результат принимается только если
позиция продвинулась, затем все результаты стабильно дедуплицируются.

#### `_with_count(base, state, path, count)`

Возвращает копию state:

- вставляет `count` во внешний source order;
- добавляет `VariationStep(kind="repeat", path=path,
  selected=(count,))`.

## `expressions.py`: полиморфный координатор AST

### `ExpressionMatcher`

Composition root для atom/group/repeat matchers и реализация
`ExpressionWalker`.

#### `__init__(parameter_types, dispatch_order=None, states=None)`

Создаёт:

- общий `WalkStateSet`;
- `LiteralExpressionMatcher`;
- `ParameterExpressionMatcher`;
- `GroupExpressionMatcher`;
- `RepeatExpressionMatcher`, использующий тот же state set.

#### `match(expression, state, command, diagnostics, *, path)`

Полностью вычисляет `walk()`, стабильно удаляет дубликаты и возвращает
`tuple[WalkState, ...]`. Это основной вход для одного ребра графа.

#### `walk(expression, state, command, diagnostics, *, path)`

Диспетчеризует по фактическому типу AST:

- `Literal` → literal matcher;
- `Parameter` → parameter matcher;
- `Group` → group matcher с `walker=self`;
- `Repeat` → repeat matcher с `walker=self`.

Для неизвестного `Node` выбрасывает `TypeError("unsupported pattern node:
...")`. Результат — ленивый iterator.

#### `sequence(expressions, state, command, diagnostics, *, path)`

Последовательно применяет tuple выражений:

1. frontier начинается с одного исходного state;
2. каждое выражение применяется ко всем состояниям frontier;
3. полученный декартов набор стабильно дедуплицируется;
4. если frontier пуст, обработка досрочно завершается.

`path` дополняется индексом каждого выражения. Возвращается вычисленный tuple.
Именно этот метод обеспечивает backtracking внутри альтернатив: все
промежуточные варианты продолжаются независимо.

## `matcher.py`: обход объединённого графа

### `_Traversal`

```python
@dataclass(frozen=True, slots=True)
class _Traversal:
    node: CommandNode
    state: WalkState
    route_ids: frozenset[int] | None
    depth: int
```

Внутренний кадр рекурсивного обхода:

- `node` — текущий узел графа;
- `state` — состояние после префикса;
- `route_ids` — маршруты, совместимые со всем уже пройденным путём; `None` в
  корне означает отсутствие начального ограничения;
- `depth` — номер graph step, используется в trace path.

### `CommandMatcher`

Низкоуровневый recognizer одной команды. Он не обрабатывает отступ,
`line_number` или пустую строку — это ответственность `CommandLineParser`.

#### `__init__(graph, parameter_types, expression_matcher=None, resolver=None, candidate_set=None, error_factory=None)`

Обязательные зависимости:

- `graph: CommandGraph` — неизменяемый merged prefix graph;
- `parameter_types: ParameterTypeRegistry` — тот же набор типов, с которым
  компилировался graph.

Опциональные зависимости позволяют тестировать компоненты отдельно.
По умолчанию matcher создаёт `CommandSuggester` для данного graph/registry и
передаёт его в `CommandErrorFactory`. Через `error_factory` обе стратегии
runtime-диагностики можно заменить вместе.

#### `match(text, *, span_offset=0)`

```python
def match(
    self,
    text: str,
    *,
    span_offset: int = 0,
) -> ResolvedMatch | ParseError
```

Создаёт `CommandText`, diagnostics и список candidates, затем запускает `_walk`
из root с `WalkState()`.

- Если найден хотя бы один terminal candidate, кандидаты дедуплицируются и
  передаются `MatchResolver.resolve()`.
- Если полных кандидатов нет, `CommandErrorFactory` возвращает подробный
  syntax/unknown `ParseError` и, когда это уместно, top-5 suggestions.
- `span_offset` не влияет на matching; он только сдвигает публичные позиции.

Метод не проверяет тип `text` и знак `span_offset`: корректный внешний контракт
обеспечивает `CommandLineParser`.

#### `_walk(traversal, command, diagnostics, candidates)`

Рекурсивный DFS по графу.

1. Проверяет, находится ли cursor в конце команды.
2. В конце пересекает `node.accepting_routes` с активными `route_ids` и
   добавляет `Candidate` для каждого допустимого terminal route.
3. Там же записывает в diagnostics возможные следующие литералы. Если
   expression edges отсутствуют, завершает ветку.
4. Если вход ещё остался, но node уже terminal, записывает ожидание
   `"end of command"`.
5. `_edges()` выбирает потенциальные рёбра.
6. Для каждого ребра пересекает его route IDs с активными. Это критически
   важно: общие graph nodes не позволяют «начать одним паттерном, а закончить
   другим».
7. Сопоставляет expression и рекурсивно продолжает каждый resulting state.
   TEXT-policy при необходимости применяется внутри
   `ParameterExpressionMatcher`.

В candidates попадают как валидные, так и validation-rejected полные маршруты.
Частичный маршрут никогда не становится кандидатом.

#### `_edges(node, command, position)`

Читает текущий токен и ищет literal edge по ASCII-lower ключу.

- Если подходящего literal нет, возвращает только `expression_edges`.
- Если есть, возвращает tuple `(literal, *expression_edges)`.

Таким образом literal пробуется первым, но generic/parameter ветви не
отбрасываются преждевременно. Окончательный выбор делает Pareto resolver после
проверки полного продолжения.

Если candidates после дедупликации отсутствуют, `match()` передаёт
`command.value`, diagnostics и `span_offset` в `self._errors.create()`.
Построение сообщения и recommendations вынесено из graph walker в
`runtime_errors.py`.

## `suggestions.py`: похожие literal-led команды

Suggestion subsystem является отдельным диагностическим индексом. Он не
добавляет graph routes, не создаёт успешные candidates и никак не влияет на
приоритеты matcher-а. Его единственный результат — до пяти строк
`original_pattern` для `ParseError.suggestions`.

### `_TemplateAtom`

Один элемент облегчённого поискового шаблона:

- `literal` хранит ASCII-lower keyword;
- `declaration` хранит `ParameterDeclaration`;
- одновременно заполнено только одно поле.

`from_node(node)` принимает только `Literal | Parameter`. Для parameter
проверяется тип declaration; неизвестный объект означает внутреннее нарушение
инварианта и приводит к `TypeError`.

### `_SuggestionTemplate`

Immutable tuple атомов одной поисковой вариации. Это не runtime route:
template нужен только для fuzzy comparison и может представлять один из
характерных вариантов group/repeat.

### `_SuggestionPattern`

Объединяет `pattern_index`, исходную строку и tuple searchable templates
одного source pattern.

### `SuggestionTemplateFactory`

Создаёт ограниченное множество searchable variations из AST. Конструктор
принимает `maximum_templates=64` и отклоняет неположительный лимит.

#### `create(pattern)`

Рекурсивно преобразует `pattern.ast`, удаляет дубликаты и оставляет только
templates, первый atom которых является literal. Поэтому чистый pattern
`STRING<1-20> activate` не попадает в индекс. Pattern с optional parameter
может попасть, если существует literal-led variation, например
`[ STRING<1-20> ] display clock`.

#### `_sequence(sequence)` и `_node(node)`

`_sequence()` строит декартово произведение вариантов последовательных
AST-узлов. `_node()` маршрутизирует `Literal`, `Parameter`, `Group` и
`Repeat`; неизвестный node приводит к `TypeError`.

#### `_group(group)` и `_set_order(alternatives)`

- one-choice group добавляет templates всех alternatives;
- optional-one дополнительно добавляет пустой template;
- set group добавляет одиночные alternatives, canonical order и reverse
  order;
- optional-set также добавляет пустой вариант.

Это намеренно bounded-представление, а не полное перечисление всех
перестановок set group.

#### `_repeat(repeat)`

Для поиска достаточно характерных количеств: `minimum`, один элемент при
`minimum == 0`, и ближайшее большее допустимое количество. Каждый вариант
строится тем же bounded product.

#### `_product(left, right)` и `_unique(templates)`

Helpers стабильно удаляют дубликаты и обрезают результат по
`maximum_templates`.

### `SuggestionCatalog`

Строит immutable index один раз при создании `CommandMatcher`.

#### `__init__(graph, template_factory=None)`

Проходит source patterns в JSON order, создаёт `_SuggestionPattern` только при
наличии literal-led template и строит индекс
`root literal → pattern entries`.

Bare/root `TEXT<min-max>` и другие чистые parameter-first patterns templates
не имеют и в index отсутствуют.

#### `root_keywords`

Возвращает отсортированный tuple всех индексированных root keywords.

#### `candidates(nearby_roots)`

Собирает candidate set только для точного или похожего root. Совпадение только
по нерoot keyword недостаточно. Secondary tokens анализирует последующий
`CommandSimilarity.could_be_relevant()`, поэтому шумовой точный suffix не
может заранее исключить более близкий fuzzy pattern. Entry order стабилен.

### `TokenDistance`

Сравнивает отдельные tokens без сторонних библиотек.

- `distance(left, right)` вычисляет edit distance; соседняя перестановка
  символов считается одной операцией;
- `cost(left, right)` нормализует distance в диапазон `0..1000`;
- `similarity(left, right)` возвращает `1000 - cost`.

### `_SimilarityScore`

Сортируемый immutable score из четырёх частей:

1. normalized edit cost;
2. отрицательное число точных prefix literals;
3. отрицательное число всех точных literal matches;
4. разница в количестве tokens.

Меньший tuple означает более релевантный template.

### `CommandSimilarity`

Сравнивает concrete CLI tokens с atoms searchable template.

#### `score(query_tokens, template)`

Dynamic programming допускает вставку, удаление, замену и перестановку двух
соседних literals. Для parameter atom вызывается тот же
`ParameterTypeRegistry.probe()`, что и в matcher:

- `VALID` parameter имеет низкую стоимость;
- `INVALID` остаётся похожим, но получает штраф;
- `NOT_APPLICABLE` получает высокий штраф.

Итог нормализуется по максимальной длине, после чего добавляются exact-prefix,
exact-match и token-count tie-breakers.

#### `is_relevant(query_tokens, template, score)`

Финально отсекает случайные совпадения по normalized cost. Явная опечатка в
root допускается даже при сильном расхождении хвоста: root keyword является
наиболее важным сигналом предполагаемой команды.

#### `could_be_relevant(query_tokens, template)`

Дешёвый pre-filter перед dynamic programming. Template обязан иметь точный или
похожий literal root. Многотокенная команда дополнительно должна иметь
точный/похожий secondary literal либо применимый parameter slot. Так общий
root вроде `display` не превращает случайный хвост в пять произвольных
рекомендаций.

#### `root_is_near(query, root)`

Быстрый pre-filter root keywords. Допустимое edit distance зависит от длины
query token, а normalized similarity не должна быть ниже порога.

#### Внутренние helpers

- `_substitution_cost(atom, token)` выбирает literal distance или результат
  parameter probe;
- `_missing_cost(atom)` назначает разные штрафы literal и parameter;
- `_is_transposition(...)` распознаёт перестановку соседних literal tokens;
- `_exact_literal_matches(...)` считает multiset-пересечение literals;
- `_exact_literal_prefix(...)` считает непрерывный точный literal prefix.

### `CommandSuggester`

Публичная внутри matching-слоя стратегия top-5 recommendations.

#### `__init__(graph, parameter_types, catalog=None, similarity=None)`

По умолчанию создаёт `SuggestionCatalog` и `CommandSimilarity`. Инъекция обеих
стратегий позволяет независимо тестировать orchestration.

#### `suggest(command, limit=5)`

Разбивает строку по whitespace, переводит tokens в ASCII-lower и возвращает
не больше `min(limit, 5)` source patterns. Пустая команда или лимит меньше
одного дают пустой tuple. Последние 256 нормализованных запросов кешируются;
кеш не входит в публичный result.

#### `_rank(query_tokens)`

1. выбирает nearby roots;
2. получает кандидатов из индекса;
3. через `_searchable()` оставляет templates с релевантным suffix; если таких
   нет вообще, сильная root-опечатка включает fallback по root;
4. находит лучший template каждого source pattern;
5. отбрасывает нерелевантные и exact textual self-suggestions;
6. сортирует по `_SimilarityScore`, затем по JSON pattern index;
7. удаляет одинаковые `original_pattern`;
8. возвращает максимум пять строк.

Группы и placeholders в рекомендациях не разворачиваются: пользователь видит
ровно исходный pattern из JSON.

## `runtime_errors.py`: английские сообщения matching errors

### `ExpectedElementFormatter`

Преобразует структурированный tuple ожиданий в короткую английскую фразу.

#### `format(expected, maximum=5)`

Берёт `ExpectedElement.description`, показывает не более `maximum` элементов,
а остаток сворачивает в `"and N more options"`. Пустой tuple превращается в
`"a valid continuation"`.

#### `_join(items)`

Использует английские `or` и Oxford comma для одного, двух или нескольких
видимых ожиданий.

### `CommandErrorFactory`

Строит `UNKNOWN_COMMAND` и `SYNTAX_ERROR`. Validation errors остаются
ответственностью `ValidationErrorFactory`.

#### `__init__(suggester, expected_formatter=None)`

Получает обязательный `CommandSuggester` и опциональную стратегию форматирования
ожиданий.

#### `create(command, diagnostics, *, span_offset)`

1. выбирает `UNKNOWN_COMMAND`, если furthest position равна `0`, иначе
   `SYNTAX_ERROR`;
2. переводит `position` и `ExpectedElement.position` в координаты исходной
   строки;
3. вызывает suggester только при
   `diagnostics.allows_keyword_suggestions`;
4. создаёт `ParseError` с `expected` и `suggestions`.

`failures`, `candidate_patterns` и `candidate_variations` для этих ошибок
пусты.

#### `_message(command, code, position, expected, suggestions, suggestions_allowed)`

Все сообщения формируются на английском.

При наличии рекомендаций формат стабилен:

```text
Command 'dispaly clock' was not recognized. Did you mean:
  1. display clock
Reason: No complete command pattern accepted the first token.
```

Для syntax error строка `Reason` сообщает 1-based column и кратко перечисляет
ожидания. Структурированные `position` и `expected` остаются полными и
используют 0-based string offsets.

Если рекомендации отсутствуют, message объясняет причину. Для обычного
`UNKNOWN_COMMAND` это `"No similar literal command patterns were found."`;
для syntax error без релевантного кандидата — аналогичное сообщение о
недостаточном сходстве. Если recommendations были подавлены применимым
parameter-led route, текст явно сообщает и об этом.

#### `_numbered(suggestions)` и `_no_suggestion_message(code, suggestions_allowed)`

Первый helper форматирует нумерованный top-5 block. Второй добавляет явное
объяснение пустого результата или подавления keyword suggestions.

## `resolver.py`: окончательное разрешение совпадений

### `ResolvedMatch`

```python
@dataclass(frozen=True, slots=True)
class ResolvedMatch:
    status: MatchStatus
    primary_match: PatternMatch
    alternative_matches: tuple[PatternMatch, ...]
```

Внутренний успешный результат:

- `status` — `UNIQUE`, `EQUIVALENT` или `AMBIGUOUS`;
- `primary_match` — первый представитель по порядку исходного JSON;
- `alternative_matches` — все остальные сохранившиеся совпадения.

`CommandLineParser` оборачивает его в публичный `ParsedCommand`, добавляя
исходную строку, отступ и номер строки.

### `MatchResolver`

#### `__init__(frontier=None, matches=None, validation_errors=None)`

Принимает подменяемые:

- `CandidateFrontier`;
- `PatternMatchSet`;
- `ValidationErrorFactory`.

#### `resolve(candidates, graph, *, span_offset)`

```python
def resolve(...) -> ResolvedMatch | ParseError
```

Алгоритм:

1. Делит candidates на:
   - `valid` — `state.rejected` пуст;
   - `invalid` — есть хотя бы один rejected-параметр.
2. Вычисляет Pareto frontier валидных кандидатов.
3. Среди invalid оставляет applicable frontier: все rejected в кандидате
   должны иметь статус `INVALID`, а не `NOT_APPLICABLE`.
4. Удаляет из valid frontier кандидаты, доминируемые applicable-invalid
   кандидатом.
5. Если валидные были, но все заблокированы, строит validation error только из
   фактических blockers.
6. Если валидных результатов нет, строит validation error из applicable
   frontier; если он пуст — из Pareto frontier всех invalid, включая
   `NOT_APPLICABLE`.
7. Иначе создаёт public pattern matches и вычисляет success status.

Пример блокировки:

```text
паттерны: value INTEGER<1-10>
          value STRING<1-20>
вход:     value 99
```

Числовой маршрут имеет dispatch `(0, 3)`, применим, но нарушает диапазон. Он
доминирует общий валидный `(0, 4)`, поэтому результат — `VALIDATION_ERROR`, а
не успешный `STRING`.

Вход `value abc` даёт `NOT_APPLICABLE` для integer; он не блокирует
`STRING`.

Аналогично, при паттернах `peer X.X.X.X` и `peer STRING<1-64>` вход
`peer 192.0.2.999` возвращает `VALIDATION_ERROR` от `ipv4-address`, а
`peer router.example.com` успешно использует generic string route.

#### `_applicable_frontier(invalid)`

Выбирает invalid candidates, у которых:

- rejected tuple не пуст;
- каждый `RejectedParameter.applicable` равен `True`.

Возвращает их `CandidateFrontier.select()`.

#### `_unblocked(valid, invalid)`

Возвращает valid candidates, для которых ни один applicable-invalid dispatch
не доминирует их dispatch.

#### `_blockers(invalid, valid)`

Обратная операция: возвращает invalid candidates, доминирующие хотя бы один
валидный. Они используются для точного validation report.

#### `_status(matches)`

- один match → `MatchStatus.UNIQUE`;
- несколько с одинаковой semantic signature →
  `MatchStatus.EQUIVALENT`;
- несколько разных → `MatchStatus.AMBIGUOUS`.

## `matches.py`: публичные совпадения и provenance

### `PatternMatchFactory`

#### `create(candidate, route, *, span_offset)`

Преобразует один валидный internal candidate в `PatternMatch`.

- `variation` строится как `" ".join(state.parts)`;
- полный trace равен `route.static_trace + state.trace`: compile-time
  развёрнутые choices не теряются;
- `variation_id` вычисляется `_variation_id()`;
- каждый `CapturedParameter` преобразуется в `ParameterValue`;
- span параметра сдвигается на `span_offset`;
- `pattern_id`, `pattern_index` и `original_pattern` берутся из
  `RouteSource.pattern`.

Формат результата:

```python
PatternMatch(
    pattern_id="pattern:<20 hex chars>:<duplicate occurrence>",
    pattern_index=17,
    original_pattern="interface { STRING<1-63> | ENUM{Vlanif,...} }",
    variation="interface ENUM{Vlanif,...}",
    variation_id="<20 hex chars>",
    parameters=(ParameterValue(...),),
    trace=(VariationStep(...),),
)
```

#### `_variation_id(pattern_id, variation, trace)`

Создаёт material из:

- pattern ID;
- ASCII-lower variation;
- `repr(trace)`.

Элементы соединяются NUL-символом, хешируются SHA-256, возвращаются первые
20 hex-символов. ID детерминирован для одного pattern content/occurrence и
одного пути variation; перестановка несвязанных JSON-паттернов его не меняет.

### `PatternMatchSet`

#### `__init__(factory=None)`

Принимает `PatternMatchFactory`.

#### `create(candidates, graph, *, span_offset)`

Сортирует кандидаты по:

1. `pattern.index` — исходный порядок в JSON;
2. `route_id`;
3. `state.source_order`;
4. порядку кандидата во входном tuple.

Затем создаёт `PatternMatch` и стабильно дедуплицирует их по `_identity()`.
Первое совпадение после этой операции становится primary match.

#### `equivalent(matches)`

Вычисляет `_signature()` каждого match. Возвращает `True`, если количество
уникальных signatures равно одному. Для пустого tuple также получится
`False` (`len(set()) != 1`), но resolver вызывает метод только для нескольких
matches.

#### `_identity(match)`

Ключ удаления полностью дублирующихся совпадений:

- `pattern_id`;
- ASCII-lower variation;
- для каждого параметра: `type_id`, declaration, raw.

Trace и normalized намеренно не входят. Два пути одного pattern, давшие
одинаковую видимую variation и те же raw-параметры, представлены первым
совпадением.

#### `_signature(match)`

Семантическая сигнатура для статуса `equivalent`:

- ASCII-lower variation;
- для каждого параметра: `type_id`, declaration, raw,
  `repr(normalized)`.

`pattern_id` не входит, поэтому одинаковые результаты разных исходных
паттернов считаются equivalent, но каждый pattern match всё равно возвращается
в `alternative_matches`.

## `validation.py`: построение ошибок параметров

### `ValidationErrorFactory`

#### `create(candidates, graph, *, span_offset)`

Сортирует кандидаты по JSON pattern index и route ID. Для каждого:

- сохраняет `route.pattern.original`;
- строит candidate variation через `" ".join(state.parts)`;
- преобразует все rejected-параметры в `ValidationFailure`.

После этого стабильно удаляет дубли failures, patterns и variations и
возвращает:

```python
ParseError(
    code=ErrorCode.VALIDATION_ERROR,
    message=<подробное английское описание>,
    position=<start первого failure или None>,
    failures=(...),
    candidate_patterns=(...),
    candidate_variations=(...),
    suggestions=(),
)
```

Message называет один matched pattern или количество candidate patterns,
указывает число невалидных значений и включает до трёх причин. Если failures
больше, остаток сворачивается в `"and N more failures"`. Полный набор никогда
не теряется и остаётся в структурированном поле `failures`.

`expected` и `suggestions` для validation error остаются пустыми: command
structure уже найдена, поэтому предлагать похожие keywords неправильно.

#### `_failure(rejected, *, span_offset)`

Создаёт один `ValidationFailure`.

- Для `NOT_APPLICABLE` message:
  `"value does not match <declaration>"`, `reason_code="not_applicable"`,
  `expected=<declaration>` и `actual=<raw token>`.
- Для `INVALID` используется `ParameterIssue.message`; если plugin нарушил
  ожидаемый контракт и issue отсутствует, fallback —
  `"invalid parameter value"` с `reason_code="invalid_value"`.
- При наличии `ParameterIssue` его `code`, `expected` и `actual` переносятся
  в одноимённые machine-readable поля failure.
- `type_id`, declaration и raw переносятся без изменений.
- token span сдвигается на `span_offset`.

#### `_message(failures, patterns)`

Формирует human-readable summary, не пытаясь заменить структурированные поля.
Для одного source pattern использует его полное `repr`; для нескольких
сообщает число candidates. Каждая видимая причина включает raw value,
declaration и validator message. Завершающая фраза направляет API-пользователя
к `failures`, `candidate_patterns` и `candidate_variations`.

## `__init__.py`: экспорт подсистемы

Модуль экспортирует:

```python
from vrp_parser.matching import CommandMatcher, ResolvedMatch
```

`__all__ = ["CommandMatcher", "ResolvedMatch"]`.

Это экспорт для связи внутренних слоёв проекта. Обычному пользователю следует
импортировать `CommandLineParser` и `ConfigurationParser` непосредственно из
`vrp_parser`.

## Backtracking и защита от ложного crossover

Граф объединяет одинаковые префиксы разных паттернов, но каждое ребро хранит
`frozenset[route_id]`. При переходе `_walk()` пересекает набор ребра с набором
маршрутов, допустимых до этого перехода.

Например, при условном графе:

```text
a STRING x
a INTEGER y
```

общее начало `a` и общий graph node не позволяют пройти `STRING`, а затем
закончить терминалом маршрута `INTEGER y`: после каждого перехода остаются
только route IDs, участвовавшие во всём префиксе.

Backtracking сохраняется на трёх уровнях:

- graph walker пробует literal edge и все expression edges;
- sequence продолжает каждое состояние от предыдущего выражения;
- groups/repeats выдают все допустимые выборы и количества.

Раннее Pareto-сокращение применяется только там, где ветви имеют одинаковую
позицию и одинаковое продолжение. Для unordered set альтернативы сокращаются
раздельно, поскольку выбранный индекс меняет будущие возможности.

## Как интерпретировать результат

### Успех

`ResolvedMatch` всегда содержит хотя бы один `PatternMatch`.

- `unique` — остался один match;
- `equivalent` — совпало несколько источников, но их variation и параметры
  семантически одинаковы;
- `ambiguous` — осталось несколько несравнимых интерпретаций.

И `equivalent`, и `ambiguous` являются успешным parsing. Все варианты доступны
в `ParsedCommand.matches`, а первый по JSON-order — в `primary_match`.

### Неизвестная команда

`ErrorCode.UNKNOWN_COMMAND` означает, что ни одна ветвь не продвинулась дальше
нулевой позиции. Bare/root `TEXT<min-max>` в позиции `0` не маскирует
неизвестные команды: он разрешён только для строк, начинающихся с `!`. Это
ограничение не относится к remainder-параметру после совпавшего keyword,
например `description TEXT<1-80>`.

Для literal-led опечатки `suggestions` может содержать до пяти релевантных
original patterns, а `message` показывает тот же список после английского
`"Did you mean:"`. Root `TEXT` и parameter-first patterns не становятся
recommendation targets. Если похожих literals нет, tuple пуст, а message прямо
объясняет отсутствие похожих команд.

### Синтаксическая ошибка

`ErrorCode.SYNTAX_ERROR` означает, что совпал некоторый префикс, но ни один
маршрут не принял строку полностью. `expected` содержит объединённые ожидания
в самой дальней позиции. Message показывает 1-based column, не более пяти
ожиданий и при наличии — top-5 suggestions. Если дальше всех продвинулся
route, начинающийся с применимого parameter, keyword recommendations
подавляются.

### Ошибка валидации

`ErrorCode.VALIDATION_ERROR` означает, что форма хотя бы одного полного
паттерна совпала, но ни один допустимый candidate не остался: validator мог
вернуть `INVALID` или, при отсутствии другой завершённой ветки,
`NOT_APPLICABLE`. Отдельный важный случай — применимый более специфичный
`INVALID` parameter блокирует менее специфичный валидный fallback. В
`failures` находятся токены, диапазоны и сообщения validator, а в
`candidate_patterns`/`candidate_variations` — все релевантные источники.
`ValidationFailure.reason_code`, `expected` и `actual` предназначены для
автоматической диагностики. `ParseError.message` даёт подробный английский
summary, но `suggestions` всегда остаётся пустым.

## Низкоуровневый пример

Этот способ полезен в тестах инфраструктуры, но не заменяет
`CommandLineParser`:

```python
from vrp_parser.compiler import PatternCompiler
from vrp_parser.matching import CommandMatcher, ResolvedMatch
from vrp_parser.parameters import default_parameter_registry
from vrp_parser.results import ParseError

registry = default_parameter_registry().freeze()
graph = PatternCompiler(registry).compile(
    (
        "preference INTEGER<1-15>",
        "preference STRING<1-20>",
    )
)
matcher = CommandMatcher(graph, registry)

outcome = matcher.match("preference 100")

if isinstance(outcome, ParseError):
    # Здесь это validation_error: INTEGER применим, но вне диапазона.
    print(outcome.code, outcome.failures)
else:
    assert isinstance(outcome, ResolvedMatch)
    print(outcome.status, outcome.primary_match)
```

При прямом вызове ответственность за согласованность registry и graph, снятие
отступа, проверку одной физической строки и корректный `span_offset` лежит на
вызывающем коде.
