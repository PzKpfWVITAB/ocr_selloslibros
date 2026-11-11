import unittest
from src.parsers.text_normalizer import TextNormalizer

class TestTextNormalizer(unittest.TestCase):
    def test_parse_numbers_in_words(self):
        self.assertEqual(TextNormalizer.parse_number_or_word("UNO"), 1)
        self.assertEqual(TextNormalizer.parse_number_or_word("DOS"), 2)
        self.assertEqual(TextNormalizer.parse_number_or_word("TRES"), 3)
        self.assertEqual(TextNormalizer.parse_number_or_word("PRIMERO"), 1)
        self.assertEqual(TextNormalizer.parse_number_or_word("PRIMER"), 1)
        self.assertEqual(TextNormalizer.parse_number_or_word("SEGUNDO"), 2)
        self.assertEqual(TextNormalizer.parse_number_or_word("TERCERO"), 3)
        self.assertEqual(TextNormalizer.parse_number_or_word("1ERO"), 1)
        self.assertEqual(TextNormalizer.parse_number_or_word("2DO"), 2)
        self.assertEqual(TextNormalizer.parse_number_or_word("UNICO"), 1)

    def test_parse_roman_numerals(self):
        self.assertEqual(TextNormalizer.parse_number_or_word("I"), 1)
        self.assertEqual(TextNormalizer.parse_number_or_word("II"), 2)
        self.assertEqual(TextNormalizer.parse_number_or_word("#I"), 2)  # Error típico de sello
        self.assertEqual(TextNormalizer.parse_number_or_word("III"), 3)
        self.assertEqual(TextNormalizer.parse_number_or_word("IV"), 4)
        self.assertEqual(TextNormalizer.parse_number_or_word("V"), 5)
        self.assertEqual(TextNormalizer.parse_number_or_word("X"), 10)

    def test_parse_with_parenthesis(self):
        self.assertEqual(TextNormalizer.parse_number_or_word("#I (DOS)"), 2)
        self.assertEqual(TextNormalizer.parse_number_or_word("1 (UNO)"), 1)
        self.assertEqual(TextNormalizer.parse_number_or_word("II (DOS)"), 2)

    def test_parse_spanish_years(self):
        self.assertEqual(TextNormalizer.parse_spanish_year("2005"), 2005)
        self.assertEqual(TextNormalizer.parse_spanish_year("DEL ANO DOS MIL CINCO"), 2005)
        self.assertEqual(TextNormalizer.parse_spanish_year("AÑO DOSMIL ONCO"), 2005)
        self.assertEqual(TextNormalizer.parse_spanish_year("10-NOVIEMBRE-2004"), 2004)
        self.assertEqual(TextNormalizer.parse_spanish_year("ANO DOS MIL"), 2000)
        self.assertEqual(TextNormalizer.parse_spanish_year("1996"), 1996)

    def test_normalize_section(self):
        self.assertEqual(TextNormalizer.normalize_section("COMERCIO"), 2)
        self.assertEqual(TextNormalizer.normalize_section("PROPIEDAD"), 1)
        self.assertEqual(TextNormalizer.normalize_section("PRIMERA"), 1)
        self.assertEqual(TextNormalizer.normalize_section("SEGUNDA"), 2)
        self.assertEqual(TextNormalizer.normalize_section("01"), 1)
        self.assertEqual(TextNormalizer.normalize_section("3"), 3)

if __name__ == "__main__":
    unittest.main()
