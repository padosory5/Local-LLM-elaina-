"""Names a person says often and a transcriber gets wrong.

The near-miss guard (brain/near_miss.py) compares a word against what the
conversation holds. Measured live, that is not enough: asked cold,

    said:   인천공항에서 미국 시애틀까지 가는데 몇 시간 걸려?
    heard:  빈천공항에서 미국 CLT까지 가는데 몇 시간 걸려?

nothing in the conversation mentioned either place, so there was nothing to
compare "빈천공항" with, and she searched for an airport that does not
exist. Well-known place names are a vocabulary of their own: an airport,
a city, a university a person travels between. A word one sound away from
one of them, that is not itself a name anyone uses, was almost certainly
that name.

Korean and English spellings are paired, so an English memory ("studies at
UW in Seattle") makes the Korean name 시애틀 something she is listening for.
The list is deliberately places and not things: a place name is a closed,
stable vocabulary, and the ones here are the ones this person's
conversations actually reach -- Korea, the Seattle area, the cities people
fly between.
"""

from __future__ import annotations

LANGUAGES = ("ko", "en")

# (Korean, English)
PLACES: tuple[tuple[str, str], ...] = (
    # Airports, including the one that was misheard.
    ("인천공항", "Incheon Airport"),
    ("인천국제공항", "Incheon International Airport"),
    ("김포공항", "Gimpo Airport"),
    ("김해공항", "Gimhae Airport"),
    ("제주공항", "Jeju Airport"),
    ("청주공항", "Cheongju Airport"),
    ("대구공항", "Daegu Airport"),
    ("나리타공항", "Narita Airport"),
    ("하네다공항", "Haneda Airport"),
    # Korea.
    ("서울", "Seoul"), ("부산", "Busan"), ("인천", "Incheon"),
    ("대구", "Daegu"), ("대전", "Daejeon"), ("광주", "Gwangju"),
    ("울산", "Ulsan"), ("수원", "Suwon"), ("제주도", "Jeju"),
    ("강릉", "Gangneung"), ("경주", "Gyeongju"), ("전주", "Jeonju"),
    ("여수", "Yeosu"), ("속초", "Sokcho"), ("강남", "Gangnam"),
    ("홍대", "Hongdae"), ("명동", "Myeongdong"), ("판교", "Pangyo"),
    ("해운대", "Haeundae"),
    # The Seattle area, where this person studies.
    ("시애틀", "Seattle"), ("벨뷰", "Bellevue"), ("레드먼드", "Redmond"),
    ("타코마", "Tacoma"), ("에버렛", "Everett"), ("베인브리지", "Bainbridge"),
    ("워싱턴대학교", "University of Washington"),
    # North America.
    ("포틀랜드", "Portland"), ("밴쿠버", "Vancouver"), ("토론토", "Toronto"),
    ("몬트리올", "Montreal"), ("뉴욕", "New York"), ("맨해튼", "Manhattan"),
    ("보스턴", "Boston"), ("시카고", "Chicago"), ("워싱턴", "Washington"),
    ("로스앤젤레스", "Los Angeles"), ("샌프란시스코", "San Francisco"),
    ("샌디에이고", "San Diego"), ("라스베이거스", "Las Vegas"),
    ("하와이", "Hawaii"), ("호놀룰루", "Honolulu"), ("애틀랜타", "Atlanta"),
    ("댈러스", "Dallas"), ("휴스턴", "Houston"), ("마이애미", "Miami"),
    ("덴버", "Denver"), ("피닉스", "Phoenix"), ("샬럿", "Charlotte"),
    ("필라델피아", "Philadelphia"), ("한인타운", "Koreatown"),
    # Asia.
    ("도쿄", "Tokyo"), ("오사카", "Osaka"), ("교토", "Kyoto"),
    ("후쿠오카", "Fukuoka"), ("삿포로", "Sapporo"), ("오키나와", "Okinawa"),
    ("베이징", "Beijing"), ("상하이", "Shanghai"), ("홍콩", "Hong Kong"),
    ("타이베이", "Taipei"), ("싱가포르", "Singapore"), ("방콕", "Bangkok"),
    ("하노이", "Hanoi"), ("다낭", "Da Nang"), ("호치민", "Ho Chi Minh"),
    ("세부", "Cebu"), ("발리", "Bali"),
    # Europe.
    ("런던", "London"), ("파리", "Paris"), ("로마", "Rome"),
    ("바르셀로나", "Barcelona"), ("마드리드", "Madrid"), ("베를린", "Berlin"),
    ("프라하", "Prague"), ("암스테르담", "Amsterdam"), ("취리히", "Zurich"),
    ("비엔나", "Vienna"),
)

# Real airport codes. A code in this set is a real place, never a slip on
# its own -- CLT is Charlotte -- though it can still sound like a place the
# person has been talking about (brain/near_miss.py asks about that).
AIRPORT_CODES = frozenset({
    "ICN", "GMP", "PUS", "CJU", "SEA", "LAX", "SFO", "JFK", "EWR", "LGA",
    "ORD", "BOS", "ATL", "DFW", "IAH", "MIA", "DEN", "PHX", "CLT", "PDX",
    "YVR", "YYZ", "HNL", "LAS", "SAN", "NRT", "HND", "KIX", "PEK", "PVG",
    "HKG", "TPE", "SIN", "BKK", "LHR", "CDG", "FRA",
})

# What comes after a place name and is part of it: "빈천 공항" is one name
# heard as two words.
HEAD_NOUNS = ("국제공항", "공항", "대학교", "터미널", "역")


def korean_names() -> tuple[str, ...]:
    return tuple(korean for korean, _ in PLACES)


def english_names() -> tuple[str, ...]:
    return tuple(english for _, english in PLACES)


def korean_for(text: str) -> tuple[str, ...]:
    """The Korean names of the places an English text mentions."""
    lowered = str(text or "").casefold()
    return tuple(
        korean for korean, english in PLACES
        if english.casefold() in lowered
    )


def english_for(text: str) -> tuple[str, ...]:
    """The English names of the places a Korean text mentions.

    The mirror of ``korean_for``, and needed once memories are written in
    the language the person used: the profile says "사용자는 워싱턴 대학교에서
    컴퓨터공학을 전공하고 있습니다", the answer says "University of
    Washington", and the grounding guard called a remembered fact an
    unverified place because no English name was anywhere in its evidence.

    Spaces are ignored on the Korean side: the pair here is written
    워싱턴대학교 and a memory says 워싱턴 대학교, which is the same place.
    """
    said = "".join(str(text or "").split())
    return tuple(
        english for korean, english in PLACES
        if korean in said
    )


def is_known(word: str) -> bool:
    """Whether a word is a place name here, in either language."""
    folded = str(word or "").casefold()
    return any(
        folded in {korean.casefold(), english.casefold()}
        for korean, english in PLACES
    ) or str(word or "").upper() in AIRPORT_CODES
