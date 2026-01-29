import re
import os
import json
import time
import requests
from typing import Optional
from dotenv import load_dotenv
from datetime import datetime
from dataclasses import dataclass

load_dotenv()

# =============================================================================
# CONFIGURATION CLASSES
# =============================================================================

@dataclass
class WooCommerceConfig:
    base_url: str = "https://wk-reifen.de/wp-json/wc/v3"
    api_headers: dict = None

    def __post_init__(self):
        self.api_headers = {
            'Authorization': f'Basic {os.getenv("WOOCOMMERCE_AUTH")}',
            'Cookie': '_fbp=fb.1.1769588395993.1698520537.AQ; _fbp=fb.1.1769553440211.1191208120.AQ'
        }


@dataclass
class OpenAIConfig:
    api_key: str = os.getenv("OPENAI_API_KEY")
    model: str = "gpt-4o"
    max_tokens: int = 150
    temperature: float = 0.7


@dataclass
class ScriptConfig:
    delay_between_requests: float = 1.0
    max_retries: int = 3
    checkpoint_file: str = "processed_items.json"
    log_file: str = "update_logs.json"


# Initialize configurations
woo_config = WooCommerceConfig()
openai_config = OpenAIConfig()
script_config = ScriptConfig()


# =============================================================================
# CHECKPOINT & LOGGING UTILITIES
# =============================================================================

class CheckpointManager:
    """Manages processed item IDs to prevent duplicate processing"""

    def __init__(self, checkpoint_file: str):
        self.checkpoint_file = checkpoint_file
        self.processed_ids = self._load_checkpoint()

    def _load_checkpoint(self) -> set:
        """Load processed IDs from checkpoint file"""
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return set(data.get("processed_ids", []))
            except (json.JSONDecodeError, IOError) as e:
                print(f"⚠ Warning: Could not load checkpoint file: {e}")
                return set()
        return set()

    def is_processed(self, item_id: int) -> bool:
        """Check if an item has already been processed"""
        return item_id in self.processed_ids

    def mark_processed(self, item_id: int):
        """Mark an item as processed and save to file"""
        self.processed_ids.add(item_id)
        self._save_checkpoint()

    def _save_checkpoint(self):
        """Save processed IDs to checkpoint file"""
        try:
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump({"processed_ids": list(self.processed_ids)}, f, indent=2)
        except IOError as e:
            print(f"⚠ Warning: Could not save checkpoint file: {e}")


class UpdateLogger:
    """Logs all update operations with detailed information"""

    def __init__(self, log_file: str):
        self.log_file = log_file
        self.logs = self._load_logs()

    def _load_logs(self) -> list:
        """Load existing logs from file"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def log_update(self, item_id: int, previous_title: str, new_title: str,
                   new_slug: str, seo_title: str,
                   previous_description: str, new_description: str):
        """Log an update operation with all updated fields"""
        log_entry = {
            "item_id": item_id,
            "previous_title": previous_title,
            "new_title": new_title,
            "new_slug": new_slug,
            "seo_title": seo_title,
            "previous_description": previous_description,
            "new_description": new_description,
            "updated_at": datetime.now().isoformat()
        }
        self.logs.append(log_entry)
        self._save_logs()

    def _save_logs(self):
        """Save logs to file"""
        try:
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(self.logs, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"⚠ Warning: Could not save log file: {e}")


# =============================================================================
# WOOCOMMERCE API FUNCTIONS
# =============================================================================

class WooCommerceAPI:

    def __init__(self, config: WooCommerceConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.api_headers)

    def get_all_products(self, per_page: int = 100) -> list[dict]:
        print("📦 Fetching products from WooCommerce...")

        all_products = []
        page = 1

        while True:
            try:
                print(f"   Fetching page {page}...", end=" ")

                response = self.session.get(
                    f"{self.config.base_url}/products",
                    params={
                        "per_page": per_page,
                        "page": page
                    },
                    timeout=60
                )
                response.raise_for_status()

                products = response.json()

                if not products:
                    # No more products, exit the loop
                    print("No more products.")
                    break

                all_products.extend(products)
                print(f"Got {len(products)} products (Total: {len(all_products)})")

                # Check if we've reached the last page
                # WooCommerce returns total pages in headers
                total_pages = int(response.headers.get('X-WP-TotalPages', 1))

                if page >= total_pages:
                    break

                page += 1

                # Small delay between pagination requests
                time.sleep(0.5)

            except requests.exceptions.RequestException as e:
                print(f"\n   ✗ Error fetching page {page}: {e}")
                raise

        print(f"📦 Total products fetched: {len(all_products)}")
        return all_products

    def update_product(self, product_id: int, update_data: dict) -> bool:
        """Update a product with the provided data"""
        for attempt in range(script_config.max_retries):
            try:
                response = self.session.put(
                    f"{self.config.base_url}/products/{product_id}",
                    json=update_data,
                    timeout=30
                )
                response.raise_for_status()
                return True

            except requests.exceptions.RequestException as e:
                print(f"   ⚠ Attempt {attempt + 1}/{script_config.max_retries} failed: {e}")
                if attempt < script_config.max_retries - 1:
                    time.sleep(script_config.delay_between_requests * 2)
                else:
                    print(f"   ✗ Failed to update product {product_id} after {script_config.max_retries} attempts")
                    return False

        return False


# =============================================================================
# OPENAI API FUNCTIONS
# =============================================================================

class OpenAIAPI:
    def __init__(self, config: OpenAIConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json"
        })
        self.api_url = "https://api.openai.com/v1/chat/completions"

    def refine_title(self, original_title: str) -> Optional[str]:
        """Refine a product title using OpenAI"""
        system_prompt = """Sie sind SEO-Experte und spezialisiert auf Produkttitel für einen deutschen Reifenhändler.

Ihre Aufgabe ist es, die Produkttitel so zu optimieren, dass sie:

- SEO-freundlich und keywordoptimiert sind
- klar und informativ sind
- professionell und ansprechend sind
- prägnant sind (maximal 60 Zeichen)

Bitte senden Sie uns ausschließlich den optimierten Titel."""

        user_prompt = f"Optimieren Sie diesen Produkttitel für eine bessere Suchmaschinenoptimierung: {original_title}"

        return self._make_openai_request(system_prompt, user_prompt)

    def refine_description(self, original_description: str, product_title: str) -> Optional[str]:
        """Refine an og_description using OpenAI for SEO optimization"""
        system_prompt = """Du bist ein SEO-Experte für E-Commerce-Reifenprodukte.

Erstelle ausschließlich eine Meta-Beschreibung für ein Produkt.

Die Meta-Beschreibung muss:
- sachlich und neutral formuliert sein
- SEO-optimiert und CTR-orientiert sein
- keine Leistungsversprechen enthalten (kein Grip, keine Sicherheit, kein Komfort)
- keine Qualitätsaussagen oder Bewertungen enthalten
- ausschließlich kauf-, preis- und verfügbarkeitsorientierte Keywords verwenden
- rechtlich unkritisch (UWG-konform) sein
- für Massenautomatisierung (100.000+ Produkte) geeignet sein
- maximal 140-150 Zeichen lang sein

Erlaubte Keywords (variabel kombinieren, nicht wiederholen):
günstig, besonders günstig, preiswert, attraktiver Preis, fairer Preis,
online kaufen, jetzt online kaufen, direkt online bestellen,
sofort verfügbar, direkt verfügbar, ab Lager lieferbar,
schneller Versand, zügiger Versand, zeitnaher Versand

Nicht erlaubt:
hervorragend, top, sicher, Sicherheit, Grip, leise, Komfort,
beste Qualität, Premium, Testsieger, Empfehlung, Top-Qualität

Es wird nur die Meta-Beschreibung zurückgegeben.
Keine Erklärungen, keine Überschriften, kein zusätzlicher Text."""

        user_prompt = f"""Erstellen Sie eine SEO-optimierte Produktbeschreibung für dieses Reifenprodukt..
Product title: {product_title}
Current description: {original_description}"""

        return self._make_openai_request(system_prompt, user_prompt)

    def _make_openai_request(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Make a request to OpenAI API"""
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature
        }

        for attempt in range(script_config.max_retries):
            try:
                response = self.session.post(
                    self.api_url,
                    json=payload,
                    timeout=60
                )
                response.raise_for_status()

                result = response.json()
                content = result["choices"][0]["message"]["content"].strip()

                # Remove quotes if the AI wrapped the content in them
                content = content.strip('"\'')

                return content

            except requests.exceptions.RequestException as e:
                print(f"   ⚠ OpenAI API attempt {attempt + 1}/{script_config.max_retries} failed: {e}")
                if attempt < script_config.max_retries - 1:
                    time.sleep(script_config.delay_between_requests * 2)
                else:
                    print(f"   ✗ Failed after {script_config.max_retries} attempts")
                    return None
            except (KeyError, IndexError) as e:
                print(f"   ✗ Unexpected response format from OpenAI: {e}")
                return None

        return None


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def generate_slug(title: str) -> str:
    """Generate a slug from the title using the formula: new_title.lower().replace(' ', '-')"""
    # Convert to lowercase and replace spaces with hyphens
    slug = title.lower().replace(' ', '-')

    # Replace German umlauts
    slug = slug.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue')
    slug = slug.replace('ß', 'ss')

    # Remove any character that's not alphanumeric or hyphen
    slug = re.sub(r'[^a-z0-9\-]', '', slug)

    # Remove multiple consecutive hyphens
    slug = re.sub(r'-+', '-', slug)

    # Remove leading/trailing hyphens
    slug = slug.strip('-')

    return slug


def get_og_description(product: dict) -> str:
    """Extract og_description from product data"""
    try:
        return product.get("yoast_head_json", {}).get("og_description", "")
    except (AttributeError, TypeError):
        return ""


# =============================================================================
# MAIN AUTOMATION WORKFLOW
# =============================================================================

class TitleRefinementAutomation:

    def __init__(self):
        self.woo_api = WooCommerceAPI(woo_config)
        self.openai_api = OpenAIAPI(openai_config)
        self.checkpoint = CheckpointManager(script_config.checkpoint_file)
        self.logger = UpdateLogger(script_config.log_file)
        self.stats = {
            "total": 0,
            "processed": 0,
            "updated": 0,
            "skipped_duplicate": 0,
            "skipped_unchanged": 0,
            "failed": 0
        }

    def process_single_product(self, product: dict) -> bool:
        """Process a single product: refine title, generate slug, refine description"""
        product_id = product["id"]
        original_title = product["name"]
        original_description = get_og_description(product)

        print(f"\n🔄 Processing Product ID: {product_id}")
        print(f"   Original Title: {original_title}")

        # Check if already processed (duplicate prevention)
        if self.checkpoint.is_processed(product_id):
            print(f"   ⏭ Already processed, skipping...")
            self.stats["skipped_duplicate"] += 1
            return True

        # Step 1: Refine the title using OpenAI
        refined_title = self.openai_api.refine_title(original_title)

        if refined_title is None:
            print(f"   ✗ Failed to get refined title")
            self.stats["failed"] += 1
            return False

        # Step 2: Generate new slug from the refined title
        new_slug = generate_slug(refined_title)

        print(f"   Refined Title: {refined_title}")
        print(f"   New Slug: {new_slug}")

        # Step 3: Refine the description using OpenAI
        refined_description = self.openai_api.refine_description(
            original_description, refined_title
        )

        if refined_description is None:
            print(f"   ✗ Failed to get refined description")
            self.stats["failed"] += 1
            return False

        print(f"   Original Description: {original_description[:80]}..." if len(original_description) > 80 else f"   Original Description: {original_description}")
        print(f"   Refined Description: {refined_description[:80]}..." if len(refined_description) > 80 else f"   Refined Description: {refined_description}")

        # Skip if nothing has changed
        if (refined_title.lower() == original_title.lower() and
            refined_description == original_description):
            print(f"   ⏭ Content unchanged, skipping update")
            self.stats["skipped_unchanged"] += 1
            self.checkpoint.mark_processed(product_id)
            return True

        update_data = {
            "name": refined_title,
            "slug": new_slug,
            "meta_data": [
                {
                    "key": "_yoast_wpseo_title",
                    "value": refined_title
                },
                {
                    "key": "_yoast_wpseo_metadesc",
                    "value": refined_description
                },
                {
                    "key": "_yoast_wpseo_focuskw",
                    "value": refined_title
                }
            ]
        }

        print(f"   SEO Title: {refined_title}")

        if self.woo_api.update_product(product_id, update_data):
            print(f"   ✓ Successfully updated!")
            self.stats["updated"] += 1

            # Log the update
            self.logger.log_update(
                item_id=product_id,
                previous_title=original_title,
                new_title=refined_title,
                new_slug=new_slug,
                seo_title=refined_title,
                previous_description=original_description,
                new_description=refined_description
            )

            # Mark as processed
            self.checkpoint.mark_processed(product_id)
            return True
        else:
            self.stats["failed"] += 1
            return False

    def run(self, dry_run: bool = False, limit: Optional[int] = None):
        """Run the automation workflow"""
        print("=" * 60)
        print("🚀 WooCommerce & OpenAI Title Refinement Automation")
        print("=" * 60)

        if dry_run:
            print("⚠️  DRY RUN MODE - No products will be updated")

        # Step 1: Fetch all products
        try:
            products = self.woo_api.get_all_products()
        except Exception as e:
            print(f"❌ Failed to fetch products: {e}")
            return

        if not products:
            print("ℹ️  No products found to process")
            return

        # Apply limit if specified
        if limit:
            products = products[:limit]
            print(f"ℹ️  Processing limited to {limit} products")

        self.stats["total"] = len(products)

        # Step 2: Process each product
        print(f"\n📝 Processing {len(products)} products...")

        for i, product in enumerate(products, 1):
            print(f"\n[{i}/{len(products)}]", end="")

            if dry_run:
                # In dry run, just show what would happen
                product_id = product["id"]
                original_title = product["name"]
                original_description = get_og_description(product)

                if self.checkpoint.is_processed(product_id):
                    print(f" Product ID: {product_id} - Already processed, would skip")
                    self.stats["skipped_duplicate"] += 1
                    continue

                print(f" Product ID: {product_id}")
                print(f"   Original Title: {original_title}")

                refined_title = self.openai_api.refine_title(original_title)
                if refined_title:
                    new_slug = generate_slug(refined_title)
                    print(f"   Would refine to: {refined_title}")
                    print(f"   New slug would be: {new_slug}")

                    refined_description = self.openai_api.refine_description(
                        original_description, refined_title
                    )
                    if refined_description:
                        print(f"   New description would be: {refined_description[:80]}...")
                    self.stats["processed"] += 1
                else:
                    self.stats["failed"] += 1
            else:
                self.process_single_product(product)
                self.stats["processed"] += 1

            # Add delay between products to avoid rate limiting
            time.sleep(script_config.delay_between_requests)

        # Step 3: Print summary
        self.print_summary()

    def print_summary(self):
        """Print a summary of the automation results"""
        print("\n" + "=" * 60)
        print("📊 AUTOMATION SUMMARY")
        print("=" * 60)
        print(f"   Total Products:       {self.stats['total']}")
        print(f"   Processed:            {self.stats['processed']}")
        print(f"   Updated:              {self.stats['updated']}")
        print(f"   Skipped (duplicate):  {self.stats['skipped_duplicate']}")
        print(f"   Skipped (unchanged):  {self.stats['skipped_unchanged']}")
        print(f"   Failed:               {self.stats['failed']}")
        print("=" * 60)
        print(f"\n📁 Checkpoint file: {script_config.checkpoint_file}")
        print(f"📁 Log file: {script_config.log_file}")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    """Main entry point for the script"""

    # Create automation instance
    automation = TitleRefinementAutomation()

    automation.run(
        dry_run=False,
        limit=None
    )


if __name__ == "__main__":
    main()
