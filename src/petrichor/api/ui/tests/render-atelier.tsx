import { fireEvent, render, screen } from "@testing-library/react";
import App from "../src/App";

/* Web v1 (issue #205): App now opens on the public landing zone. Tests that
   exercise the workshop render <App /> then navigate to the "atelier" zone
   through the site navigation, exactly like a visitor would. Web v1 (issue
   #209): the workshop mounts only once /auth/me has answered (account menu),
   so the helper waits for the Amorçage step. */

export async function renderAtelier() {
  const utils = render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Atelier" }));
  await screen.findByRole("heading", { name: "Amorçage" });
  return utils;
}
