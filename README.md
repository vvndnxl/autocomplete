# Autocomplete API

Сервис автодополнения слов по префиксу на основе префиксного дерева.

## Реализация
Внутри узлов префиксного дерева храняться списки из 50 лучших подсказок, что решает проблему обхода всего поддерева. Для мгновенного обновления весов в узлах хранится хэш-мапа. Обновления топа происходят через атомарную подмену ссылки для параллельного Lock-Free чтения.

## Тесты

Запуск стандартных тестов:
```bash
pip install -r tests/requirements-test.txt
python -m pytest -vv tests
```

Запуск теста с нагрузкой
```bash
pip install -r tests/requirements-test.txt
docker build -t autocomplete-api .
docker run -d -p 8000:8000 autocomplete-api
wrk -t12 -c400 -d30s "http://localhost:8000/autocomplete?prefix=a&limit=10"
```

## Результаты теста с нагрузкой
```text
12 threads and 400 connections
    Thread Stats    Avg         Stdev       Max         +/- Stdev
    Latency         267.95ms    36.04ms     470.46ms    84.84%
    Req/Sec         126.63      91.50       323.00      59.42%

  44156 requests in 30.07s, 6.57MB read
Requests/sec:   1468.58
Transfer/sec:   223.73KB
```

Пропускная способность стабильная. Задержки связаны с валидацей FastAPI и ожиданием запросов в очереди пула потоков.
