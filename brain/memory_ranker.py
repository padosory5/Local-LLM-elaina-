from datetime import datetime


class MemoryRanker:

    def __init__(self):

        self.similarity_weight = 0.50
        self.importance_weight = 0.25
        self.recency_weight = 0.15
        self.access_weight = 0.10

    def rank(self, memories):

        now = datetime.utcnow()

        ranked = []

        for memory in memories:

            importance = memory.importance / 10
            access = min(memory.access_count / 20, 1)

            days = (now - memory.last_accessed).days
            recency = max(0, 1 - days / 30)

            similarity = getattr(memory, "similarity", 1.0)

            score = (
                similarity * self.similarity_weight +
                importance * self.importance_weight +
                recency * self.recency_weight +
                access * self.access_weight
            )

            ranked.append((score, memory))

        ranked.sort(reverse=True, key=lambda x: x[0])

        return [m for score, m in ranked[:5]]