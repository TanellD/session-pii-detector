# tests/

Тесты и регрессионный набор edge-кейсов (каталог — вопрос B6), только синтетика. Каждый найденный баг → новый тест-кейс.

Исследовательские проверки: `python -X utf8 -m unittest discover -s tests`.

HTTP-сервис проверяется отдельно: установите `requirements-service-test.txt` в `.venv-service`, затем выполните `.venv-service\Scripts\python.exe -X utf8 -m unittest discover -s tests/service -v`. Используются подставные детекторы и линкер, без моделей, данных и внешних API. Проверка запущенного процесса/контейнера: `.venv-service\Scripts\python.exe -X utf8 tools/smoke_service.py`.
