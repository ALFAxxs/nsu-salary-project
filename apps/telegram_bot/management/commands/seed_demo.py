"""
Seed the acceptance-test scenario (spec §52):
  head office + 15 branches, some employees per branch, and role-scoped admins.

Usage:  python manage.py seed_demo
Idempotent-ish: safe to run once on an empty DB.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role, User
from apps.employees.models import Employee
from apps.organizations.models import OrganizationUnit, UnitType


class Command(BaseCommand):
    help = "Seed head office, 15 branches, employees and admins."

    @transaction.atomic
    def handle(self, *args, **options):
        hq, _ = OrganizationUnit.objects.get_or_create(
            code="HQ",
            defaults={"name": "Bosh Office", "type": UnitType.HEAD_OFFICE,
                      "is_head_office": True},
        )
        self.stdout.write(self.style.SUCCESS(f"Head office: {hq}"))

        branches = []
        for i in range(1, 16):
            code = f"BR-{i:02d}"
            b, _ = OrganizationUnit.objects.get_or_create(
                code=code,
                defaults={"name": f"Filial {i}", "type": UnitType.BRANCH},
            )
            branches.append(b)
        self.stdout.write(self.style.SUCCESS(f"Branches: {len(branches)}"))

        # Employees: a few per unit.
        def make_employees(unit, start):
            for n in range(1, 6):
                code = f"{unit.code}-E{n}"
                phone = f"9989012{start + n:05d}"
                Employee.objects.get_or_create(
                    employee_code=code,
                    defaults={
                        "full_name": f"{unit.name} Xodim {n}",
                        "phone": phone,
                        "organization_unit": unit,
                    },
                )

        make_employees(hq, 0)
        for idx, b in enumerate(branches, start=1):
            make_employees(b, idx * 100)
        self.stdout.write(self.style.SUCCESS(f"Employees: {Employee.objects.count()}"))

        # Super admin.
        if not User.objects.filter(username="superadmin").exists():
            User.objects.create_superuser(
                username="superadmin", password="Admin!2345",
                role=Role.SUPER_ADMIN,
            )
        # Head office admin.
        hq_user, hq_created = User.objects.get_or_create(
            username="hqadmin",
            defaults={"role": Role.HEAD_OFFICE_ADMIN, "organization_unit": hq},
        )
        if hq_created:
            hq_user.set_password("Hqadmin!2345")
            hq_user.save()
        # Branch 5 admin (for the isolation test).
        br5 = branches[4]
        u, created = User.objects.get_or_create(
            username="branch5",
            defaults={"role": Role.BRANCH_ADMIN, "organization_unit": br5},
        )
        if created:
            u.set_password("Branch!2345")
            u.save()

        self.stdout.write(self.style.SUCCESS(
            "Admins: superadmin / hqadmin / branch5 (parollar kodda)"
        ))
        self.stdout.write(self.style.SUCCESS("Seed complete."))
