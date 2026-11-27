"""Воспроизводимый случайный baseline; не включается в сервис по умолчанию."""
import random

from tools.ownership import owner_ids
from .components import LinkResult


class RandomLinker:
    """Каждый известный человек, NEW и UNKNOWN имеют вероятность 1/(n+2).

    Экземпляр создаётся на сессию. Новые люди текущего сообщения доступны
    следующим кандидатам. Совместные владельцы и служебные контакты не выводятся.
    """

    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def link(self, message, candidates, known_facts):
        people = sorted({p for fact in known_facts for p in owner_ids(fact.person_id)})
        result = []
        for _ in candidates:
            choice = self.rng.randrange(len(people) + 2)
            if choice < len(people):
                person = people[choice]
            elif choice == len(people):
                number = 1
                while f'random-{number}' in people:
                    number += 1
                person = f'random-{number}'
                people.append(person)
                people.sort()
            else:
                person = None
            result.append(person)
        return LinkResult(tuple(result))
