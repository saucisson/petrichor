import "@testing-library/jest-dom/vitest";

// jsdom does not implement Blob.text() yet; the app uses it to read the PNML
// file. Shim it through FileReader so the component code stays standard.
if (typeof Blob.prototype.text !== "function") {
  Blob.prototype.text = function (this: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(this);
    });
  };
}
