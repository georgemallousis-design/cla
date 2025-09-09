#!/usr/bin/env python3
"""
Sample Data Script - Προσθήκη δεδομένων για testing
Τρέξε αυτό το script για να προσθέσεις sample data στη βάση
"""

from database import DatabaseManager


def add_sample_data():
    """Προσθήκη sample data για testing."""

    print("Προσθήκη sample data...")

    # Αρχικοποίηση database
    db = DatabaseManager()

    # Προσθήκη sample customers
    print("Προσθήκη customers...")
    customers = [
        (1234, "Γιάννης Παπαδόπουλος", "2101234567", "giannis@email.com", "VIP πελάτης"),
        (5678, "Μαρία Κωνσταντίνου", "6971234567", "maria@email.com", "Τακτικός πελάτης"),
        (9999, "Δημήτρης Αντωνίου", "2109876543", "dimitris@email.com", "Νέος πελάτης"),
        (1111, "Αννα Γεωργίου", "6987654321", "anna@email.com", ""),
        (2222, "Κώστας Μιχαήλ", "2108765432", "kostas@email.com", "Πελάτης εταιρείας")
    ]

    for pin, name, phone, email, notes in customers:
        db.add_customer(pin, name, phone, email, notes)
        print(f"  ✅ Προστέθηκε πελάτης: {name} (PIN: {pin})")

    # Προσθήκη sample materials
    print("\nΠροσθήκη materials...")
    materials = [
        {
            'name': 'Dell Laptop XPS 13',
            'model': 'XPS-13-9310',
            'manufacturer': 'Dell',
            'description': 'Premium ultrabook με Intel i7',
            'price': 1299.99,
            'category': 'Electronics',
            'is_used': False
        },
        {
            'name': 'HP Printer LaserJet',
            'model': 'LaserJet Pro M404dn',
            'manufacturer': 'HP',
            'description': 'Μονόχρωμος laser printer',
            'price': 199.99,
            'category': 'Electronics',
            'is_used': False
        },
        {
            'name': 'Apple MacBook Air',
            'model': 'MBA-M2-2022',
            'manufacturer': 'Apple',
            'description': 'MacBook Air με M2 chip',
            'price': 1199.99,
            'category': 'Electronics',
            'is_used': False
        },
        {
            'name': 'Wireless Mouse',
            'model': 'MX Master 3',
            'manufacturer': 'Logitech',
            'description': 'Επαγγελματικό ασύρματο ποντίκι',
            'price': 89.99,
            'category': 'Accessories',
            'is_used': False
        },
        {
            'name': 'USB Hub',
            'model': 'UH720',
            'manufacturer': 'TP-Link',
            'description': '7-port USB 3.0 hub',
            'price': 24.99,
            'category': 'Accessories',
            'is_used': False
        },
        {
            'name': 'Dell Monitor',
            'model': 'U2419H',
            'manufacturer': 'Dell',
            'description': '24" IPS monitor',
            'price': 199.99,
            'category': 'Electronics',
            'is_used': True
        }
    ]

    for material_data in materials:
        material_id = db.add_material(**material_data)
        if material_id:
            print(f"  ✅ Προστέθηκε υλικό: {material_data['name']} (ID: {material_id})")

            # Προσθήκη κάποιων serial numbers
            serials = []
            for i in range(3):  # 3 serials ανά υλικό
                serial = f"{material_data['model']}-{material_id:03d}{i + 1:02d}"
                serials.append(serial)

            added_serials = db.add_serials_to_material(material_id, serials)
            print(f"    📦 Προστέθηκαν {added_serials} serial numbers")

    # Προσθήκη sample assignments
    print("\nΠροσθήκη assignments...")

    # Πάρε κάποια serials για assignment
    try:
        # Assign Dell laptop σε Γιάννη
        dell_serials = db.get_material_serials(1)  # Assuming Dell laptop has ID 1
        if dell_serials:
            db.assign_serial_to_customer(dell_serials[0]['id'], 1234, "MallousG", "Assignment για testing")
            print(f"  ✅ Ανατέθηκε serial {dell_serials[0]['serial_number']} στον πελάτη 1234")

        # Assign HP printer σε Μαρία
        hp_serials = db.get_material_serials(2)  # Assuming HP printer has ID 2
        if hp_serials:
            db.assign_serial_to_customer(hp_serials[0]['id'], 5678, "MallousG", "Assignment για testing")
            print(f"  ✅ Ανατέθηκε serial {hp_serials[0]['serial_number']} στον πελάτη 5678")

    except Exception as e:
        print(f"  ⚠️  Δεν ήταν δυνατή η δημιουργία assignments: {e}")

    print("\n🎉 Sample data προστέθηκαν με επιτυχία!")
    print("\nΤώρα μπορείς να τρέξεις την εφαρμογή:")
    print("python app.py")
    print("\nLogin credentials:")
    print("Username: MallousG")
    print("Password: MallousG")


if __name__ == "__main__":
    add_sample_data()