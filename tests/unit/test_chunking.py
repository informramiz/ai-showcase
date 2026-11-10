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


def test_empty_sections_produce_no_chunks() -> None:
    assert chunk_document("# Only a heading\n\n") == []


def test_derive_title_uses_first_heading() -> None:
    assert derive_title("text\n## Parental leave\n# Later", "fallback") == "Parental leave"


def test_derive_title_falls_back_and_truncates() -> None:
    assert derive_title("no headings", "x" * 200) == "x" * 120
