# Публичный API, результаты, CLI и сериализация

Этот документ описывает production-сущности из:

- `src/vrp_parser/api.py`;
- `src/vrp_parser/results.py`;
- `src/vrp_parser/serialization.py`;
- `src/vrp_parser/cli.py`;
- `src/vrp_parser/__init__.py` и `src/vrp_parser/__main__.py`;
- ручного стенда `manual_test.py`.

Все позиции в строках — нулевые, а диапазоны имеют полуоткрытый формат
`[start, end)`: символ с индексом `end` в диапазон не входит. Номера физических
строк начинаются с единицы.

## `CommandLineParser`

Публичная точка входа для компиляции каталога паттернов и разбора одной
физической CLI-строки.

### `CommandLineParser.__init__`

```python
CommandLineParser(
    pattern_document: Mapping[str, Any],
    *,
    parameter_types: ParameterTypeRegistry | None = None,
)
```

Входной `pattern_document` должен иметь формат:

```json
{
  "commands": [
    "#",
    "TEXT<1-4096>",
    "interface STRING<1-63>"
  ]
}
```

Правила документа:

- корень — mapping/object;
- ключ `commands` обязателен;
- значение `commands` — непустая последовательность строк;
- пустые и состоящие только из пробелов паттерны запрещены;
- порядок строк значим: он определяет первичный результат при равных матчах;
- одинаковые паттерны допустимы и сохраняются как разные source entries.

Если `parameter_types` не передан, создаётся стандартный registry. Переданный
registry клонируется и замораживается, поэтому его последующие изменения не
влияют на готовый parser.

Конструктор:

1. проверяет формат документа;
2. парсит все паттерны в AST;
3. применяет runtime policy;
4. строит общий immutable command graph;
5. создаёт matcher.

Возможные исключения:

- `PatternDocumentError` — неверная структура документа;
- `PatternCompilationError` — один или несколько ошибочных паттернов.

### `command_count`

```python
parser.command_count -> int
```

Количество source-паттернов в скомпилированном документе. Дубликаты считаются
отдельно.

### `command_graph`

```python
parser.command_graph -> CommandGraph
```

Read-only доступ к внутреннему общему графу. Нужен для диагностики,
визуализации и тестов. Обычному пользователю для парсинга не требуется.

### `parameter_types`

```python
parser.parameter_types -> ParameterTypeRegistry
```

Возвращает замороженный registry, которым был скомпилирован parser.
`register()` для него завершится ошибкой.

### `parse`

```python
parser.parse(line: str, line_number: int = 1) -> LineResult
```

Разбирает ровно одну физическую строку без `\n` или `\r`.

Вход:

- `line` — исходная строка, включая отступ и завершающие пробелы;
- `line_number` — положительный целый номер, записываемый в результат.

Поведение:

- исходный `raw` сохраняется без изменений;
- ведущие whitespace-символы сохраняются в `indent`;
- завершающие пробелы не участвуют в распознавании;
- пустая или whitespace-only строка возвращает `BlankLine`;
- успешный матч, включая неоднозначный, возвращает `ParsedCommand`;
- неизвестная, синтаксически незавершённая или невалидная команда возвращает
  `ErrorLine`.

Исключения относятся только к неправильному вызову API:

- `TypeError`, если `line` не строка;
- `ValueError`, если передано несколько физических строк;
- `TypeError`, если `line_number` не `int` или является `bool`;
- `ValueError`, если `line_number < 1`.

Ошибочная CLI-команда не вызывает исключение: она представляется объектом
`ErrorLine`.

### `from_json`

```python
CommandLineParser.from_json(
    source: str,
    *,
    parameter_types: ParameterTypeRegistry | None = None,
) -> CommandLineParser
```

Принимает JSON-текст, проверяет, что JSON-корень является object/mapping, и
вызывает конструктор. Невалидный JSON преобразуется в `PatternDocumentError`.

### `from_json_file`

```python
CommandLineParser.from_json_file(
    path: str | pathlib.Path,
    *,
    parameter_types: ParameterTypeRegistry | None = None,
) -> CommandLineParser
```

Читает UTF-8 файл и делегирует `from_json()`.

Дополнительные ошибки:

- `OSError`/`FileNotFoundError` — файл невозможно прочитать;
- `UnicodeDecodeError` — файл не является корректным UTF-8.

### Внутренние методы `CommandLineParser`

Эти методы являются деталями реализации и не предназначены для вызова
пользователем.

| Метод | Назначение и результат |
| --- | --- |
| `_parsed(line_number, raw, indent, outcome)` | Преобразует внутренний `ResolvedMatch` в публичный `ParsedCommand`. |
| `_validate_input(line, line_number)` | Проверяет типы, отсутствие line terminator и положительный номер; возвращает `None` или поднимает `TypeError`/`ValueError`. |
| `_indent_end(line)` | Возвращает индекс первого не-whitespace символа или `len(line)`. |
| `_commands(document)` | Извлекает и валидирует `commands`, возвращает `tuple[str, ...]`. |

## `ConfigurationParser`

Обёртка над уже скомпилированным `CommandLineParser`. Она не строит второй
граф и не меняет правила распознавания.

### `ConfigurationParser.__init__`

```python
ConfigurationParser(
    line_parser: CommandLineParser,
    report_factory: ParseReportFactory | None = None,
)
```

- `line_parser` повторно используется для каждой физической строки;
- `report_factory` — необязательная dependency-injection точка для сборки
  итогового отчёта.

### `line_parser`

```python
configuration_parser.line_parser -> CommandLineParser
```

Возвращает исходный parser строк.

### `parse`

```python
configuration_parser.parse(content: str) -> ParseReport
```

Принимает весь конфигурационный текст. Поддерживает `LF`, `CRLF` и `CR`.

Особенности:

- пустой текст даёт отчёт с `lines == ()`;
- один завершающий line terminator не создаёт фиктивную дополнительную строку;
- пустые строки внутри файла сохраняются как `BlankLine`;
- ошибка одной строки не останавливает разбор следующих;
- номера строк назначаются начиная с `1`.

Если `content` не строка, поднимается `TypeError`.

### `_physical_lines`

```python
ConfigurationParser._physical_lines(content: str) -> tuple[str, ...]
```

Внутренний splitter для трёх видов line terminator. Возвращает строки без
самих terminator-символов.

## Формат успешного результата

### `MatchStatus`

`StrEnum`, поэтому его значения непосредственно сериализуются как строки.

| Значение | Смысл |
| --- | --- |
| `UNIQUE = "unique"` | Осталась одна лучшая интерпретация. |
| `EQUIVALENT = "equivalent"` | Несколько source-паттернов описывают одинаковую интерпретацию. |
| `AMBIGUOUS = "ambiguous"` | Остались разные, но одинаково приоритетные интерпретации. Это успех, а не ошибка. |

### `TextSpan`

```python
TextSpan(start: int, end: int)
```

Полуоткрытый диапазон в оригинальной строке, включая учёт отступа.
`__post_init__()` требует `0 <= start <= end`, иначе поднимает `ValueError`.

### `ParameterValue`

```python
ParameterValue(
    type_id: str,
    declaration: str,
    raw: str,
    normalized: Any,
    span: TextSpan,
)
```

| Поле | Формат |
| --- | --- |
| `type_id` | Стабильный ID типа: например `integer`, `date-iso`, `enum`. |
| `declaration` | Placeholder из паттерна, например `INTEGER<1-15>`. |
| `raw` | Реальный фрагмент CLI без преобразования. |
| `normalized` | Результат validator: `int`, строка, `None` или custom object. |
| `span` | Позиция `raw` в исходной физической строке. |

### `VariationStep`

```python
VariationStep(
    kind: Literal["choice", "optional", "set", "repeat", "enum"],
    path: str,
    selected: tuple[int | str, ...] = (),
)
```

Описывает одно решение при получении конкретной variation.

| `kind` | Формат `selected` |
| --- | --- |
| `choice` | Индекс выбранной альтернативы. |
| `optional` | Пустой tuple при пропуске или индекс выбранной альтернативы. |
| `set` | Индексы альтернатив в порядке их появления во входной CLI-строке. |
| `repeat` | Единственное целое число — фактическое количество повторов. |
| `enum` | Нормализованное строковое значение enum. |

`path` — стабильный внутренний адрес узла/шага, полезный для сравнения и
диагностики.

### `PatternMatch`

```python
PatternMatch(
    pattern_id: str,
    pattern_index: int,
    original_pattern: str,
    variation: str,
    variation_id: str,
    parameters: tuple[ParameterValue, ...] = (),
    trace: tuple[VariationStep, ...] = (),
)
```

| Поле | Смысл |
| --- | --- |
| `pattern_id` | Content-based ID source-паттерна с номером дубликата. |
| `pattern_index` | Индекс паттерна в JSON-массиве `commands`. |
| `original_pattern` | Исходная строка паттерна без изменений. |
| `variation` | Выбранный линейный путь; литералы приведены к canonical ASCII lowercase, параметры остаются declarations. |
| `variation_id` | Стабильный hash от pattern ID, variation и trace. |
| `parameters` | Значения параметров данного матча. |
| `trace` | Структурированные решения групп, повторов и enum. |

### `ParsedCommand`

```python
ParsedCommand(
    line_number: int,
    raw: str,
    indent: str,
    status: MatchStatus,
    primary_match: PatternMatch,
    alternative_matches: tuple[PatternMatch, ...] = (),
)
```

Автоматическое поле `kind == "command"`.

Properties:

- `parsed -> True` — в том числе при `status == AMBIGUOUS`;
- `matches -> tuple[PatternMatch, ...]` — primary и все alternatives;
- `parameters -> tuple[ParameterValue, ...]` — shortcut к параметрам primary
  match.

Primary выбирается по порядку source-паттернов и вариаций, но альтернативы не
теряются.

## Пустые и ошибочные строки

### `BlankLine`

```python
BlankLine(
    line_number: int,
    raw: str,
    indent: str,
)
```

Имеет автоматическое `kind == "blank"`. `indent` равен всей строке.

### `ErrorCode`

| Значение | Когда используется |
| --- | --- |
| `UNKNOWN_COMMAND = "unknown_command"` | Ни один command prefix не распознан. |
| `SYNTAX_ERROR = "syntax_error"` | Prefix распознан, но ни один маршрут не завершился. |
| `VALIDATION_ERROR = "validation_error"` | Структура команды завершилась, но параметры отклонены validator-ами. |

### `ExpectedElement`

```python
ExpectedElement(description: str, position: int)
```

Ожидаемый literal/placeholder в самой дальней достигнутой позиции.

### `ValidationFailure`

```python
ValidationFailure(
    type_id: str,
    declaration: str,
    raw: str,
    span: TextSpan,
    message: str,
)
```

Описывает один невалидный параметр: тип, declaration, фактическое значение,
позицию и человекочитаемую причину.

### `ParseError`

```python
ParseError(
    code: ErrorCode,
    message: str,
    position: int | None = None,
    expected: tuple[ExpectedElement, ...] = (),
    failures: tuple[ValidationFailure, ...] = (),
    candidate_patterns: tuple[str, ...] = (),
    candidate_variations: tuple[str, ...] = (),
)
```

- для syntax/unknown основными полями являются `position` и `expected`;
- для validation основными полями являются `failures`,
  `candidate_patterns` и `candidate_variations`.

### `ErrorLine`

```python
ErrorLine(
    line_number: int,
    raw: str,
    indent: str,
    error: ParseError,
)
```

Имеет `kind == "error"` и property `parsed -> False`.

### `LineResult`

Type alias:

```python
LineResult = BlankLine | ParsedCommand | ErrorLine
```

Для безопасной обработки используйте `isinstance()`.

## Отчёт полного конфигурационного файла

### `ParseSummary`

```python
ParseSummary(
    total: int,
    blank: int,
    commands: int,
    ambiguous: int,
    errors: int,
)
```

`commands` включает `unique`, `equivalent` и `ambiguous`.

### `ParseReport`

```python
ParseReport(
    lines: tuple[LineResult, ...],
    summary: ParseSummary,
)
```

#### `has_errors`

Возвращает `True`, когда `summary.errors > 0`.

#### `to_dict`

Возвращает JSON-совместимый mapping:

- dataclass преобразуется в object;
- tuple/list — в array;
- mapping — в object со строковыми ключами;
- set/frozenset — в стабильно отсортированный array;
- неизвестное custom normalized value — в `str(value)`;
- `None` остаётся JSON `null`.

Объекты исходного `ParseReport` при этом не меняются.

### `ParseReportFactory`

#### `create`

```python
ParseReportFactory.create(
    lines: tuple[LineResult, ...],
) -> ParseReport
```

Подсчитывает все поля `ParseSummary` и создаёт immutable `ParseReport`.
Не выполняет повторный парсинг.

## `JsonValueConverter`

Internal service, используемый `ParseReport.to_dict()`.

### `convert`

```python
JsonValueConverter.convert(value: Any) -> Any
```

Рекурсивно преобразует значение по правилам из секции `to_dict`. Метод
гарантирует удобный для обычного `json.dumps()` результат для поддерживаемого
дерева и string fallback для plugin-объектов.

## CLI

После установки package доступна команда `vrp-parser`; без установки можно
использовать `PYTHONPATH=src python3 -m vrp_parser`.

### `JsonOutput`

#### `write`

```python
JsonOutput.write(value: Any) -> None
```

Печатает UTF-8 JSON в stdout с отступом в два пробела. `ensure_ascii=False`
сохраняет читаемые Unicode-символы, `default=str` страхует custom values.

### `_parser`

```python
_parser() -> argparse.ArgumentParser
```

Создаёт parser с обязательными подкомандами:

```text
vrp-parser check-patterns PATTERNS_JSON
vrp-parser parse --patterns PATTERNS_JSON --config CONFIG_FILE
```

### `main`

```python
main(argv: list[str] | None = None) -> int
```

| Код | Значение |
| --- | --- |
| `0` | Каталог корректен или конфигурация разобрана без line errors. |
| `1` | Конфигурация разобрана, но содержит хотя бы один `ErrorLine`. |
| `2` | Перехваченный `OSError`, ошибка структуры JSON или компиляции паттернов. |

Оба входных файла читаются как UTF-8. `UnicodeDecodeError` не входит в
обрабатываемый `except` функции `main()` и поэтому сейчас выходит наружу, а не
преобразуется в JSON с кодом `2`.

Для поставляемого `data/commands.json` команда `check-patterns` печатает:

```json
{
  "status": "ok",
  "commands": 6187
}
```

`parse` печатает JSON-представление `ParseReport`.

### `__main__.py`

Вызов `python3 -m vrp_parser ...` делегирует `cli.main()` и возвращает его exit
code через `SystemExit`.

### Package exports

`src/vrp_parser/__init__.py` экспортирует только пользовательские parser,
result/error values и API расширения parameter registry. В частности, старого
фасада `VRPParser` и метода `parse_line()` в API нет.

## `manual_test.py`

Ручной стенд не является библиотечным API, но показывает реальные форматы.

| Сущность | Назначение |
| --- | --- |
| `PATTERN_DOCUMENT` | Небольшой изменяемый вручную каталог. |
| `main()` | Пытается создать line/configuration parsers из текущего scratchpad-каталога, обрабатывает hardcoded-вход и печатает результат. При невалидном экспериментальном pattern исключение компиляции выходит наружу. |

Запуск:

```bash
python3 manual_test.py
```

Аргументы командной строки стенд не обрабатывает. Для другого сценария
измените pattern document и входной текст вызова `parse()` в самом файле.
Успешный exit code не гарантируется для намеренно невалидного содержимого.
