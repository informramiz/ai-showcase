from sourcebook.services.chunking import chunk_document, derive_title, split_sentences


def test_split_sentences_breaks_on_terminal_punctuation() -> None:
    assert split_sentences("One thing. Two things! Three?") == (
        "One thing.",
        "Two things!",
        "Three?",
    )


def test_split_sentences_keeps_abbreviation_like_lowercase_runs() -> None:
    assert split_sentences("Pay e.g. lunch. Then go.") == ("Pay e.g. lunch.", "Then go.")


def test_list_items_are_one_sentence_each() -> None:
    text = "- First item. Still first.\n* Second\n3. Third"
    assert split_sentences(text) == ("First item. Still first.", "Second", "Third")


def test_blank_lines_are_ignored() -> None:
    assert split_sentences("\n\n  \nHello.\n") == ("Hello.",)


def test_chunks_follow_headings() -> None:
    chunks = chunk_document("Intro line.\n# A\nAlpha one.\n## B ##\nBeta one. Beta two.")
    assert [(c.ordinal, c.heading, c.text) for c in chunks] == [
        (0, "", "Intro line."),
        (1, "A", "Alpha one."),
        (2, "B", "Beta one.\nBeta two."),
    ]


def test_long_sections_split_on_sentence_boundaries() -> None:
    body = " ".join(f"Sentence number {i} is here." for i in range(10))
    chunks = chunk_document(f"# Long\n{body}", max_chars=60)
    assert len(chunks) > 1
    assert all(c.heading == "Long" for c in chunks)
    assert all(len(c.text) <= 60 for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_empty_input_produces_no_chunks() -> None:
    assert chunk_document("\n\n") == []


def test_heading_only_section_stays_searchable() -> None:
    chunks = chunk_document("# Only a heading\n\n")
    assert [(c.heading, c.text) for c in chunks] == [("Only a heading", "Only a heading")]


def test_long_sentence_is_split_below_the_limit() -> None:
    chunks = chunk_document("word " * 400 + "end.", max_chars=100)
    assert len(chunks) > 1
    assert all(len(c.text) <= 100 + len(c.text.splitlines()) for c in chunks)
    assert chunk_document("x" * 250, max_chars=100)[0].text.splitlines()[0] == "x" * 100


def test_derive_title_uses_first_heading() -> None:
    assert derive_title("text\n## Parental leave\n# Later", "fallback") == "Parental leave"


def test_derive_title_falls_back_and_truncates() -> None:
    assert derive_title("no headings", "x" * 200) == "x" * 120


def test_oversized_heading_is_bounded() -> None:
    heading = "H" * 5_000
    with_body = chunk_document(f"# {heading}\n\nBody text here.")
    assert all(len(c.heading) <= 200 for c in with_body)
    only = chunk_document(f"# {heading}", max_chars=100)
    assert all(len(c.text) <= 100 for c in only)
