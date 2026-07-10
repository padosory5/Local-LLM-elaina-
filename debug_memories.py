from memory.memory_manager import MemoryManager

manager = MemoryManager()

for memory in manager.get_all_memories():
    print("=" * 50)
    print("ID:", memory.id)
    print("Content:", memory.content)
    print("Category:", memory.category)
    print("Importance:", memory.importance)
    print("Access Count:", memory.access_count)