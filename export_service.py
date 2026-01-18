import csv
import io
import json
import os
from datetime import datetime, timedelta
from typing import List, Optional
from models import Invoice
from openpyxl import Workbook
import xlrd
from xlutils.copy import copy as xl_copy

class ExportService:
    def export_dgii_606(self, invoices: List[Invoice], report_rnc: Optional[str] = None, period: Optional[str] = None) -> bytes:
        """Exportación XLS usando la plantilla oficial DGII 606 (plantilla_excel/formulario.xls)."""
        template_path = os.path.join(os.path.dirname(__file__), "plantilla_excel", "formulario.xls")
        if not os.path.exists(template_path):
            raise FileNotFoundError("No se encontró la plantilla oficial para DGII 606.")

        book = xlrd.open_workbook(template_path, formatting_info=True)
        sheet_name = "Herramienta Formato 606"
        sheet_index = book.sheet_names().index(sheet_name)
        sh = book.sheet_by_name(sheet_name)

        header_row = 10
        header_map = {str(val).strip(): idx for idx, val in enumerate(sh.row_values(header_row)) if str(val).strip()}

        wb = xl_copy(book)
        ws = wb.get_sheet(sheet_index)

        # Encabezado
        rnc_value = self._only_digits(report_rnc) if report_rnc else ""
        period_value = period or self._derive_period(invoices) or ""
        ws.write(3, 2, rnc_value)
        ws.write(4, 2, period_value)
        ws.write(5, 2, len(invoices))

        start_row = 11
        for idx, inv in enumerate(invoices, start=1):
            row = start_row + (idx - 1)
            raw = self._parse_raw_data(inv.raw_extracted_data)

            vendor_tax_id = inv.vendor_tax_id or raw.get("vendor_tax_id")
            rnc = self._only_digits(vendor_tax_id)
            tipo_id = self._tipo_id_from_tax_id(rnc)

            goods_type = self._normalize_goods_type(inv.goods_services_type or raw.get("goods_services_type"))
            ncf = inv.invoice_number or raw.get("invoice_number") or ""
            ncf_modified = raw.get("ncf_modified") or ""

            fecha_comprobante = self._format_date(inv.invoice_date or raw.get("invoice_date"))
            fecha_pago = self._format_date(raw.get("payment_date"))

            total = self._to_number(inv.total_amount) or self._to_number(raw.get("total_amount"))
            tax = self._to_number(inv.tax_amount) or self._to_number(raw.get("tax_amount"))

            base = None
            if total is not None and tax is not None:
                base = total - tax
            if base is None or base < 0:
                base = self._sum_line_items(raw.get("line_items"))
            if base is None:
                base = 0.0

            amount_services = self._to_number(raw.get("services_amount"))
            amount_goods = self._to_number(raw.get("goods_amount"))
            if amount_services is None and amount_goods is None:
                amount_goods, amount_services = self._split_base_by_type(base, inv, goods_type)
            elif amount_services is None and amount_goods is not None:
                amount_services = max(base - amount_goods, 0.0)
            elif amount_goods is None and amount_services is not None:
                amount_goods = max(base - amount_services, 0.0)

            total_facturado = (amount_services or 0.0) + (amount_goods or 0.0)

            itbis_facturado = self._to_number(tax)
            itbis_retenido = self._to_number(raw.get("itbis_retenido"))
            itbis_proporcionalidad = self._to_number(raw.get("itbis_proporcionalidad"))
            itbis_llevado_costo = self._to_number(raw.get("itbis_llevado_costo"))
            itbis_percibido = self._to_number(raw.get("itbis_percibido"))

            itbis_adelantar = None
            if itbis_facturado is not None:
                itbis_adelantar = itbis_facturado - (itbis_llevado_costo or 0.0)
                if itbis_adelantar < 0:
                    itbis_adelantar = 0.0

            isr_retention_type = self._normalize_isr_retention(raw.get("isr_retention_type"))
            isr_retention_amount = self._to_number(raw.get("isr_retention_amount"))
            isr_percibido = self._to_number(raw.get("isr_percibido"))

            isc_amount = self._to_number(raw.get("isc_amount"))
            other_taxes = self._to_number(raw.get("other_taxes"))
            legal_tip = self._to_number(raw.get("legal_tip"))

            payment_method = self._normalize_payment_method(raw.get("payment_method"))

            status = self._build_606_status(
                rnc=rnc,
                ncf=ncf,
                fecha_comprobante=fecha_comprobante,
                fecha_pago=fecha_pago,
                total_facturado=total_facturado,
                itbis_facturado=itbis_facturado,
                itbis_retenido=itbis_retenido,
                isr_retention_type=isr_retention_type,
                isr_retention_amount=isr_retention_amount
            )

            self._write_606_value(ws, header_map, row, "Líneas", idx)
            self._write_606_value(ws, header_map, row, "RNC o Cédula", rnc or "")
            self._write_606_value(ws, header_map, row, "Tipo Id", tipo_id or "")
            self._write_606_value(ws, header_map, row, "Tipo Bienes y Servicios Comprados", goods_type or "")
            self._write_606_value(ws, header_map, row, "NCF", ncf or "")
            self._write_606_value(ws, header_map, row, "NCF ó Documento Modificado", ncf_modified or "")
            self._write_606_value(ws, header_map, row, "Fecha Comprobante", fecha_comprobante or "")
            self._write_606_value(ws, header_map, row, "Fecha Pago", fecha_pago or "")
            self._write_606_value(ws, header_map, row, "Monto Facturado en Servicios", self._fmt_amount(amount_services, allow_zero=True))
            self._write_606_value(ws, header_map, row, "Monto Facturado en Bienes", self._fmt_amount(amount_goods, allow_zero=True))
            self._write_606_value(ws, header_map, row, "Total Monto Facturado", self._fmt_amount(total_facturado, allow_zero=True))
            self._write_606_value(ws, header_map, row, "ITBIS Facturado", self._fmt_amount(itbis_facturado))
            self._write_606_value(ws, header_map, row, "ITBIS Retenido", self._fmt_amount(itbis_retenido))
            self._write_606_value(ws, header_map, row, "ITBIS sujeto a Proporcionalidad (Art. 349)", self._fmt_amount(itbis_proporcionalidad))
            self._write_606_value(ws, header_map, row, "ITBIS llevado al Costo", self._fmt_amount(itbis_llevado_costo))
            self._write_606_value(ws, header_map, row, "ITBIS por Adelantar", self._fmt_amount(itbis_adelantar))
            self._write_606_value(ws, header_map, row, "ITBIS percibido en compras", self._fmt_amount(itbis_percibido))
            self._write_606_value(ws, header_map, row, "Tipo de Retención en ISR", isr_retention_type or "")
            self._write_606_value(ws, header_map, row, "Monto Retención Renta", self._fmt_amount(isr_retention_amount))
            self._write_606_value(ws, header_map, row, "ISR Percibido en compras", self._fmt_amount(isr_percibido))
            self._write_606_value(ws, header_map, row, "Impuesto Selectivo al Consumo", self._fmt_amount(isc_amount))
            self._write_606_value(ws, header_map, row, "Otros Impuesto/Tasas", self._fmt_amount(other_taxes))
            self._write_606_value(ws, header_map, row, "Monto Propina Legal", self._fmt_amount(legal_tip))
            self._write_606_value(ws, header_map, row, "Forma de Pago", payment_method or "")
            self._write_606_value(ws, header_map, row, "Estatus", status)

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()

    def _parse_raw_data(self, raw):
        try:
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return {}

    def _only_digits(self, value):
        if not value:
            return None
        return "".join([c for c in str(value) if c.isdigit()])

    def _tipo_id_from_tax_id(self, tax_id):
        if not tax_id:
            return None
        if len(tax_id) == 9:
            return "1"
        if len(tax_id) == 11:
            return "2"
        return None

    def _normalize_goods_type(self, value):
        if not value:
            return None
        digits = "".join([c for c in str(value) if c.isdigit()])
        if not digits:
            return None
        if len(digits) == 1:
            digits = f"0{digits}"
        valid = {f"{i:02d}" for i in range(1, 12)}
        return digits if digits in valid else None

    def _format_date(self, value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value.strftime('%Y%m%d')
        try:
            if isinstance(value, str):
                value = value.strip()
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
                    try:
                        return datetime.strptime(value, fmt).strftime('%Y%m%d')
                    except ValueError:
                        continue
        except Exception:
            return None
        return None

    def _derive_period(self, invoices: List[Invoice]) -> Optional[str]:
        dates = [inv.invoice_date for inv in invoices if inv.invoice_date]
        if not dates:
            return None
        most_recent = max(dates)
        return most_recent.strftime('%Y%m')

    def _write_606_value(self, ws, header_map, row, header_name, value):
        col = header_map.get(header_name)
        if col is None:
            return
        ws.write(row, col, value)

    def _to_number(self, value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _fmt_amount(self, value, allow_zero=False):
        if value is None:
            return ''
        if value == 0 and not allow_zero:
            return ''
        return f"{value:.2f}"

    def _sum_line_items(self, items):
        if not isinstance(items, list):
            return None
        total = 0.0
        has_any = False
        for item in items:
            if not isinstance(item, dict):
                continue
            subtotal = self._to_number(item.get("subtotal"))
            if subtotal is None:
                continue
            total += subtotal
            has_any = True
        return total if has_any else None

    def _split_base_by_type(self, base, inv, goods_type):
        category = (inv.category or '').lower()
        goods_keywords = ['oficina', 'inventario', 'mercancia', 'mercancía', 'compras', 'equipos', 'activos', 'maquinaria']
        goods_types = {"04", "09", "10"}
        is_goods = goods_type in goods_types or any(k in category for k in goods_keywords)
        if is_goods:
            return base, 0.0
        return 0.0, base

    def _normalize_isr_retention(self, value):
        if value is None:
            return None
        raw = str(value).strip()
        if raw.isdigit():
            code = int(raw)
            return str(code) if 1 <= code <= 9 else None
        text = raw.lower()
        mapping = {
            "alquiler": "1",
            "honorario": "2",
            "servicio": "2",
            "otras rentas": "3",
            "rentas presuntas": "4",
            "intereses pagados a personas juridicas": "5",
            "intereses pagados a personas jurídicas": "5",
            "intereses pagados a personas fisicas": "6",
            "intereses pagados a personas físicas": "6",
            "proveedores del estado": "7",
            "juegos telefonicos": "8",
            "juegos telefónicos": "8",
            "ganaderia": "9",
            "ganadería": "9"
        }
        for key, code in mapping.items():
            if key in text:
                return code
        return None

    def _normalize_payment_method(self, value):
        if value is None:
            return None
        raw = str(value).strip()
        if raw.isdigit():
            code = int(raw)
            return str(code) if 1 <= code <= 7 else None
        text = raw.lower()
        if "efectivo" in text:
            return "1"
        if "cheque" in text or "transfer" in text or "depósito" in text or "deposito" in text:
            return "2"
        if "tarjeta" in text:
            return "3"
        if "crédito" in text or "credito" in text:
            return "4"
        if "permuta" in text:
            return "5"
        if "nota de crédito" in text or "nota de credito" in text:
            return "6"
        if "mixto" in text:
            return "7"
        return None

    def _build_606_status(
        self,
        rnc,
        ncf,
        fecha_comprobante,
        fecha_pago,
        total_facturado,
        itbis_facturado,
        itbis_retenido,
        isr_retention_type,
        isr_retention_amount
    ):
        issues = []
        if not rnc:
            issues.append("Falta RNC/Cédula")
        if not ncf:
            issues.append("Falta NCF")
        if not fecha_comprobante:
            issues.append("Falta fecha comprobante")
        if (itbis_retenido or isr_retention_type or isr_retention_amount) and not fecha_pago:
            issues.append("Falta fecha pago")
        if total_facturado is None or total_facturado == 0:
            issues.append("Montos en cero")
        if itbis_facturado is not None and total_facturado is not None and itbis_facturado > 0 and total_facturado < 0:
            issues.append("Total inválido")
        return "; ".join(issues) if issues else "OK"
    def export_csv_generic(self, invoices: List[Invoice]) -> str:
        """Exportación CSV estándar detallada"""
        output = io.StringIO()
        writer = csv.writer(output)
        
        headers = [
            'ID', 'Fecha', 'Proveedor', 'Número Factura', 
            'Categoría', 'Descripción', 'Base Imponible', 
            'Impuestos', 'Total', 'Moneda', 'Estado', 'Alertas'
        ]
        writer.writerow(headers)
        
        for inv in invoices:
            base = (inv.total_amount or 0) - (inv.tax_amount or 0)
            writer.writerow([
                inv.id,
                inv.invoice_date.strftime('%Y-%m-%d') if inv.invoice_date else '',
                inv.vendor_name,
                inv.invoice_number,
                inv.category,
                inv.description,
                f"{base:.2f}",
                f"{inv.tax_amount or 0:.2f}",
                f"{inv.total_amount or 0:.2f}",
                inv.currency,
                "Procesado" if inv.processed else "Pendiente",
                inv.audit_flags or ""
            ])
            
        return output.getvalue()

    def export_quickbooks(self, invoices: List[Invoice]) -> str:
        """
        Formato compatible con importación de 'Bills' en QuickBooks Online.
        Headers: Bill No,Vendor,Transaction Date,Due Date,Total,Account,Line Amount,Line Description
        """
        output = io.StringIO()
        writer = csv.writer(output)
        
        headers = [
            'Bill No', 'Vendor', 'Transaction Date', 'Due Date', 
            'Total', 'Account', 'Line Amount', 'Line Description'
        ]
        writer.writerow(headers)
        
        for inv in invoices:
            # QuickBooks requiere mapeo de cuentas. Usamos la categoría o una cuenta por defecto
            account = inv.category or "Uncategorized Expense"
            
            # Asumimos fecha de vencimiento = fecha factura + 30 días si no hay dato
            date_str = inv.invoice_date.strftime('%m/%d/%Y') if inv.invoice_date else datetime.now().strftime('%m/%d/%Y')
            
            writer.writerow([
                inv.invoice_number or f"INV-{inv.id}",
                inv.vendor_name or "Unknown Vendor",
                date_str,
                date_str, # Due Date placeholder
                f"{inv.total_amount or 0:.2f}",
                account,
                f"{inv.total_amount or 0:.2f}", # Line Amount (simplificado a 1 linea)
                inv.description or "Services provided"
            ])
            
        return output.getvalue()

    def export_contaplus(self, invoices: List[Invoice]) -> str:
        """
        Formato simplificado tipo Diario para Sage/Contaplus (Asientos).
        Col: Fecha, Cuenta, Concepto, Debe, Haber, Documento
        """
        output = io.StringIO()
        writer = csv.writer(output)
        
        headers = ['Fecha', 'Cuenta', 'Concepto', 'Debe', 'Haber', 'Documento']
        writer.writerow(headers)
        
        for inv in invoices:
            date_str = inv.invoice_date.strftime('%d/%m/%Y') if inv.invoice_date else ''
            doc_ref = inv.invoice_number or f"DOC-{inv.id}"
            total = inv.total_amount or 0
            tax = inv.tax_amount or 0
            base = total - tax
            
            # Linea 1: Gasto (Base) -> Debe
            writer.writerow([
                date_str, 
                "60000000", # Cuenta genérica de compras (debería venir de settings)
                f"Fra. {inv.vendor_name}",
                f"{base:.2f}",
                "0.00",
                doc_ref
            ])
            
            # Linea 2: IVA -> Debe
            if tax > 0:
                writer.writerow([
                    date_str,
                    "47200000", # HP IVA Soportado
                    "IVA Soportado",
                    f"{tax:.2f}",
                    "0.00",
                    doc_ref
                ])
                
            # Linea 3: Proveedor -> Haber
            writer.writerow([
                date_str,
                "40000000", # Proveedores
                f"Fra. {inv.vendor_name}",
                "0.00",
                f"{total:.2f}",
                doc_ref
            ])
            
        return output.getvalue()

    def export_quickbooks_bills(self, invoices: List[Invoice]) -> str:
        """
        Formato Bills (QuickBooks Online) con columnas estándar.
        Headers: Bill No, Vendor, Transaction Date, Due Date, Account, Line Amount, Line Description, Total, Tax Code
        """
        output = io.StringIO()
        writer = csv.writer(output)

        headers = [
            'Bill No', 'Vendor', 'Transaction Date', 'Due Date',
            'Account', 'Line Amount', 'Line Description', 'Total', 'Tax Code'
        ]
        writer.writerow(headers)

        for inv in invoices:
            date_val = inv.invoice_date or datetime.utcnow()
            date_str = date_val.strftime('%m/%d/%Y')
            due_str = (date_val + timedelta(days=30)).strftime('%m/%d/%Y')
            total = inv.total_amount or 0
            tax = inv.tax_amount or 0
            base = total - tax
            account = inv.category or "Expenses"

            writer.writerow([
                inv.invoice_number or f"INV-{inv.id}",
                inv.vendor_name or "Unknown Vendor",
                date_str,
                due_str,
                account,
                f"{base:.2f}",
                inv.description or "Services / Goods",
                f"{total:.2f}",
                ""
            ])

        return output.getvalue()

    def export_xero_bills(self, invoices: List[Invoice]) -> str:
        """
        Formato Bills (Xero) - CSV compatible con importación estándar.
        Headers: Contact Name, Invoice Number, Invoice Date, Due Date, Description, Quantity, Unit Amount, Account Code, Tax Type, Currency
        """
        output = io.StringIO()
        writer = csv.writer(output)

        headers = [
            'Contact Name', 'Invoice Number', 'Invoice Date', 'Due Date',
            'Description', 'Quantity', 'Unit Amount', 'Account Code', 'Tax Type', 'Currency'
        ]
        writer.writerow(headers)

        for inv in invoices:
            date_val = inv.invoice_date or datetime.utcnow()
            date_str = date_val.strftime('%Y-%m-%d')
            due_str = (date_val + timedelta(days=30)).strftime('%Y-%m-%d')
            total = inv.total_amount or 0
            tax = inv.tax_amount or 0
            base = total - tax
            account = inv.category or "Expenses"

            writer.writerow([
                inv.vendor_name or "Unknown Vendor",
                inv.invoice_number or f"INV-{inv.id}",
                date_str,
                due_str,
                inv.description or "Services / Goods",
                "1",
                f"{base:.2f}",
                account,
                "",
                inv.currency or "DOP"
            ])

        return output.getvalue()

    def export_odoo_vendor_bills(self, invoices: List[Invoice]) -> str:
        """
        Formato CSV genérico para importación de facturas de proveedor en Odoo.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        headers = [
            'move_type', 'partner_id/name', 'invoice_date', 'invoice_date_due',
            'ref', 'currency_id/name',
            'invoice_line_ids/name', 'invoice_line_ids/quantity',
            'invoice_line_ids/price_unit', 'invoice_line_ids/account_id/name'
        ]
        writer.writerow(headers)

        for inv in invoices:
            date_val = inv.invoice_date or datetime.utcnow()
            date_str = date_val.strftime('%Y-%m-%d')
            due_str = (date_val + timedelta(days=30)).strftime('%Y-%m-%d')
            total = inv.total_amount or 0
            tax = inv.tax_amount or 0
            base = total - tax
            account = inv.category or "Expenses"

            writer.writerow([
                "in_invoice",
                inv.vendor_name or "Unknown Vendor",
                date_str,
                due_str,
                inv.invoice_number or f"INV-{inv.id}",
                inv.currency or "DOP",
                inv.description or "Services / Goods",
                "1",
                f"{base:.2f}",
                account
            ])

        return output.getvalue()

    def export_excel_generic(self, invoices: List[Invoice]) -> bytes:
        """Exportación Excel (.xlsx) con columnas estándar."""
        wb = Workbook()
        ws = wb.active
        ws.title = "Facturas"

        headers = [
            'ID', 'Fecha', 'Proveedor', 'NCF', 'Categoría', 'Descripción',
            'Base Imponible', 'ITBIS', 'Total', 'Moneda', 'Estado', 'Alertas',
            'RNC', 'Tipo DGII 606'
        ]
        ws.append(headers)

        for inv in invoices:
            base = (inv.total_amount or 0) - (inv.tax_amount or 0)
            ws.append([
                inv.id,
                inv.invoice_date.strftime('%Y-%m-%d') if inv.invoice_date else '',
                inv.vendor_name,
                inv.invoice_number,
                inv.category,
                inv.description,
                round(base, 2),
                round(inv.tax_amount or 0, 2),
                round(inv.total_amount or 0, 2),
                inv.currency,
                "Procesado" if inv.processed else "Pendiente",
                inv.audit_flags or "",
                inv.vendor_tax_id or "",
                inv.goods_services_type or ""
            ])

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()

    def export_json(self, invoices: List[Invoice]) -> str:
        """Exportación JSON completa"""
        data = [inv.to_dict() for inv in invoices]
        return json.dumps(data, indent=2, ensure_ascii=False)
