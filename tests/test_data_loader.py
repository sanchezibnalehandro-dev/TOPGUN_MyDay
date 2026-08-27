from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from topgun_myday.data_loader import InputDataError, load_file
from topgun_myday.metrics import MetricProfile


SYNTHETIC_COLUMNS = (
    "date",
    "barber",
    "visit_id",
    "client_id",
    "branch",
    "service_revenue",
    "product_revenue",
    "has_extra_service",
    "prebooked",
)


def valid_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["09.08.2026", "08.08.2026"],
            "barber": ["Артём", "Артём"],
            "client_id": ["А001", "А002"],
            "service": ["Стрижка", "Борода"],
            "service_revenue": [3000, 2000],
            "product_revenue": [500, 0],
            "next_booking": [" Да ", "нет"],
        }
    )


def synthetic_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["09.08.2026", "08.08.2026"],
            "barber": [" Демо-мастер А ", "Демо-мастер Б"],
            "visit_id": ["00123", "00124"],
            "client_id": ["00456", "00456"],
            "branch": [" SYNTHETIC BRANCH ", "SYNTHETIC BRANCH"],
            "service_revenue": ["3000", "2500.50"],
            "product_revenue": ["500", "0"],
            "has_extra_service": [" да ", "НЕТ"],
            "prebooked": ["ДА", " нет "],
        }
    )


class DataLoaderTests(unittest.TestCase):
    def test_demo_workbook_finds_visits_sheet_and_legacy_profile(self) -> None:
        dataset = load_file(Path("data") / "demo_topgun.xlsx")
        self.assertEqual(dataset.profile, MetricProfile.LEGACY_V01)
        self.assertEqual(dataset.sheet_name, "Визиты")
        self.assertEqual(dataset.row_count, 833)
        self.assertEqual(len(dataset.barbers), 6)
        self.assertEqual(len(dataset.branches), 2)
        self.assertEqual(dataset.date_min.strftime("%Y-%m-%d"), "2026-07-13")
        self.assertEqual(dataset.date_max.strftime("%Y-%m-%d"), "2026-08-09")

    def test_synthetic_demo_workbook_contract(self) -> None:
        dataset = load_file(Path("data") / "demo_topgun_v02.xlsx")
        self.assertEqual(dataset.profile, MetricProfile.SYNTHETIC_V02)
        self.assertEqual(dataset.sheet_name, "Визиты_SYNTHETIC")
        self.assertNotEqual(dataset.sheet_name, "README_SYNTHETIC")
        self.assertEqual(dataset.row_count, 112)
        self.assertEqual(dataset.frame["date"].nunique(), 28)
        self.assertEqual(len(dataset.barbers), 2)
        self.assertEqual(dataset.date_max.strftime("%Y-%m-%d"), "2026-08-09")
        self.assertTrue(set(SYNTHETIC_COLUMNS).issubset(dataset.frame.columns))

    def test_synthetic_normalises_flags_text_and_money(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.csv"
            synthetic_rows().to_csv(path, index=False, encoding="utf-8")
            dataset = load_file(path)
        self.assertEqual(dataset.profile, MetricProfile.SYNTHETIC_V02)
        self.assertEqual(dataset.frame["has_extra_service"].tolist(), ["Да", "Нет"])
        self.assertEqual(dataset.frame["prebooked"].tolist(), ["Да", "Нет"])
        self.assertEqual(dataset.frame["barber"].iloc[0], "Демо-мастер А")
        self.assertEqual(dataset.frame["branch"].iloc[0], "SYNTHETIC BRANCH")
        self.assertEqual(dataset.frame["service_revenue"].tolist(), [3000.0, 2500.5])
        self.assertEqual(dataset.frame["product_revenue"].tolist(), [500.0, 0.0])

    def test_synthetic_csv_preserves_leading_zero_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "идентификаторы.csv"
            synthetic_rows().to_csv(path, index=False, encoding="utf-8-sig")
            dataset = load_file(path)
        self.assertEqual(dataset.frame["visit_id"].iloc[0], "00123")
        self.assertEqual(dataset.frame["client_id"].iloc[0], "00456")
        self.assertIsInstance(dataset.frame["visit_id"].iloc[0], str)
        self.assertIsInstance(dataset.frame["client_id"].iloc[0], str)

    def test_unknown_and_blank_synthetic_flags_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            frame = synthetic_rows()
            frame.loc[0, "has_extra_service"] = " Возможно "
            frame.loc[1, "prebooked"] = ""
            path = Path(folder) / "flags.csv"
            frame.to_csv(path, index=False)
            dataset = load_file(path)
        self.assertEqual(dataset.frame["has_extra_service"].iloc[0], "Возможно")
        self.assertEqual(dataset.frame["prebooked"].iloc[1], "")

    def test_invalid_synthetic_money_is_preserved_and_not_zeroed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            frame = synthetic_rows()
            frame.loc[0, "service_revenue"] = "не число"
            frame.loc[1, "product_revenue"] = ""
            path = Path(folder) / "money.csv"
            frame.to_csv(path, index=False)
            dataset = load_file(path)
        self.assertEqual(dataset.frame["service_revenue"].iloc[0], "не число")
        self.assertEqual(dataset.frame["product_revenue"].iloc[1], "")
        self.assertNotEqual(dataset.frame["product_revenue"].iloc[1], 0)

    def test_synthetic_xlsx_nan_money_is_preserved_for_data_gap(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            frame = synthetic_rows()
            frame.loc[0, "product_revenue"] = None
            path = Path(folder) / "money_nan.xlsx"
            frame.to_excel(path, sheet_name="Данные", index=False, engine="openpyxl")
            dataset = load_file(path)
        self.assertTrue(pd.isna(dataset.frame["product_revenue"].iloc[0]))
        self.assertNotEqual(dataset.frame["product_revenue"].iloc[0], 0)

    def test_missing_synthetic_column_has_clear_profile_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.csv"
            synthetic_rows().drop(columns="has_extra_service").to_csv(path, index=False)
            with self.assertRaisesRegex(InputDataError, "has_extra_service"):
                load_file(path)

    def test_normalised_duplicate_headers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            frame = synthetic_rows()
            frame.insert(1, " Date ", frame["date"])
            path = Path(folder) / "duplicate.csv"
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(InputDataError, "дублирующиеся.*`date`"):
                load_file(path)

    def test_invalid_synthetic_date_is_loader_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            frame = synthetic_rows()
            frame.loc[0, "date"] = "не дата"
            path = Path(folder) / "date.csv"
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(InputDataError, "date.*некорректных дат"):
                load_file(path)

    def test_blank_synthetic_barber_is_loader_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            frame = synthetic_rows()
            frame.loc[0, "barber"] = " "
            path = Path(folder) / "barber.csv"
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(InputDataError, "barber.*не заполнено"):
                load_file(path)

    def test_unknown_structure_is_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unknown.csv"
            pd.DataFrame({"Дата визита": ["09.08.2026"], "Мастер": ["А"]}).to_csv(
                path, index=False
            )
            with self.assertRaisesRegex(InputDataError, "ни одному.*профил"):
                load_file(path)

    def test_multiple_synthetic_sheets_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "multi_synthetic.xlsx"
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                synthetic_rows().to_excel(writer, sheet_name="Данные 1", index=False)
                synthetic_rows().to_excel(writer, sheet_name="Данные 2", index=False)
            with self.assertRaisesRegex(InputDataError, "несколько листов SYNTHETIC_V02"):
                load_file(path)

    def test_mixed_legacy_and_synthetic_sheets_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mixed.xlsx"
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                valid_rows().to_excel(writer, sheet_name="Legacy", index=False)
                synthetic_rows().to_excel(writer, sheet_name="Synthetic", index=False)
            with self.assertRaisesRegex(InputDataError, "LEGACY_V01 и SYNTHETIC_V02"):
                load_file(path)

    def test_multiple_matching_legacy_sheets_uses_first_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "multi.xlsx"
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                valid_rows().to_excel(writer, sheet_name="Первый", index=False)
                valid_rows().to_excel(writer, sheet_name="Второй", index=False)
            dataset = load_file(path)
        self.assertEqual(dataset.profile, MetricProfile.LEGACY_V01)
        self.assertEqual(dataset.sheet_name, "Первый")
        self.assertTrue(dataset.warnings)
        self.assertIn("несколько", dataset.warnings[0])

    def test_legacy_csv_encodings_delimiters_and_cyrillic_path(self) -> None:
        cases = (
            ("utf-8", ","),
            ("utf-8-sig", "\t"),
            ("cp1251", ";"),
        )
        for encoding, separator in cases:
            with self.subTest(encoding=encoding, separator=separator):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "выгрузка с пробелом.csv"
                    valid_rows().to_csv(
                        path, sep=separator, index=False, encoding=encoding
                    )
                    dataset = load_file(path)
                self.assertEqual(dataset.profile, MetricProfile.LEGACY_V01)
                self.assertIsNone(dataset.sheet_name)
                self.assertEqual(dataset.row_count, 2)
                self.assertEqual(dataset.barbers, ("Артём",))
                self.assertEqual(dataset.frame["next_booking"].tolist(), ["Да", "Нет"])
                self.assertNotIn("discount", dataset.frame.columns)

    def test_missing_required_legacy_field_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.csv"
            valid_rows().drop(columns="next_booking").to_csv(path, index=False)
            with self.assertRaisesRegex(InputDataError, "next_booking"):
                load_file(path)

    def test_invalid_legacy_numeric_and_booking_are_rejected(self) -> None:
        for column, value, message in (
            ("service_revenue", "не число", "service_revenue"),
            ("next_booking", "Возможно", "Да.*Нет"),
        ):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as folder:
                frame = valid_rows()
                frame[column] = frame[column].astype(object)
                frame.loc[0, column] = value
                path = Path(folder) / "invalid.csv"
                frame.to_csv(path, index=False)
                with self.assertRaisesRegex(InputDataError, message):
                    load_file(path)

    def test_blank_rating_is_allowed_but_text_rating_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            frame = valid_rows()
            frame["rating"] = [4.8, None]
            path = Path(folder) / "rating.csv"
            frame.to_csv(path, index=False)
            dataset = load_file(path)
            self.assertEqual(dataset.frame["rating"].notna().sum(), 1)
            frame["rating"] = frame["rating"].astype(object)
            frame.loc[1, "rating"] = "пять"
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(InputDataError, "rating"):
                load_file(path)


if __name__ == "__main__":
    unittest.main()
