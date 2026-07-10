class ContextBuilder:

    def build(self, memories):

        sections = {
            "personal": [],
            "project": [],
            "education": [],
            "goal": [],
            "preference": [],
            "relationship": [],
            "general": []
        }

        for memory in memories:

            category = memory.category.lower()

            if category not in sections:
                category = "general"

            sections[category].append(memory.content)

        context = ""

        titles = {
            "personal": "Identity",
            "project": "Projects",
            "education": "Education",
            "goal": "Goals",
            "preference": "Preferences",
            "relationship": "Relationships",
            "general": "Other"
        }

        for category in sections:

            if len(sections[category]) == 0:
                continue

            context += f"{titles[category]}\n"

            for memory in sections[category]:
                context += f"- {memory}\n"

            context += "\n"

        return context