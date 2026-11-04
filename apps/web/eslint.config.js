// Strict TypeScript + React rules (ADR-0018): the web app lives under
// the same discipline as the Python code - enforced rules, not
// conventions. Parity map: boundaries = import-linter, type-checked
// eslint = ruff+mypy, vitest thresholds = pytest coverage floor.
import boundaries from "eslint-plugin-boundaries";
import importX from "eslint-plugin-import-x";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

const BOUNDARY_ELEMENTS = [
  { type: "lib", mode: "full", pattern: "src/lib/**" },
  { type: "hooks", mode: "full", pattern: "src/hooks/**" },
  { type: "components", mode: "full", pattern: "src/components/**" },
  { type: "app", mode: "full", pattern: "src/**" },
];

export default tseslint.config(
  { ignores: ["dist", "coverage", "eslint.config.js"] },

  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      tseslint.configs.recommendedTypeChecked,
      tseslint.configs.stylisticTypeChecked,
    ],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      "import-x": importX,
      boundaries,
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],

      // ADR-0006 parity: no `any` - use `unknown` and narrow (the
      // TypeScript equivalent of `object` + isinstance).
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unsafe-assignment": "error",
      "@typescript-eslint/no-unsafe-member-access": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/consistent-type-definitions": "error",

      // Closed sets are union types, never enums (ADR-0018).
      "no-restricted-syntax": [
        "error",
        {
          selector: "TSEnumDeclaration",
          message:
            "Enums are banned: model closed sets as union types with an exhaustive switch (ADR-0018).",
        },
      ],

      // T20 parity: no console output in application code.
      "no-console": "error",
      "no-alert": "error",

      // ADR-0004 parity: named exports only, sorted imports.
      "import-x/no-default-export": "error",
      "import-x/order": ["error", { "newlines-between": "always" }],
      "import-x/no-duplicates": "error",

      // The layer contract (ADR-0002 parity): dependencies point inward.
      "boundaries/element-types": [
        "error",
        {
          default: "disallow",
          rules: [
            { from: ["lib"], allow: [] },
            { from: ["hooks"], allow: ["lib"] },
            { from: ["components"], allow: ["lib", "hooks"] },
            { from: ["app"], allow: ["lib", "hooks", "components", "app"] },
          ],
        },
      ],
    },
    settings: {
      boundaries: {
        include: ["src/**/*.{ts,tsx}"],
        elements: BOUNDARY_ELEMENTS,
      },
    },
  },

  {
    // vite.config.ts runs in node, not the browser.
    files: ["vite.config.ts"],
    languageOptions: { globals: globals.node },
  },

  {
    // Tests are outside the layer contract; they may import anything.
    files: ["**/*.test.{ts,tsx}", "tests/**"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: {
      "boundaries/element-types": "off",
    },
  },
);
