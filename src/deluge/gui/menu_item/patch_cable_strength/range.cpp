#include "range.h"
#include "gui/menu_item/source_selection/range.h"
#include "gui/menu_item/source_selection/regular.h"
#include "gui/ui/sound_editor.h"

namespace menu_item::patch_cable_strength {
Range rangeMenu{};

ParamDescriptor Range::getLearningThing() {
	ParamDescriptor paramDescriptor;
	paramDescriptor.setToHaveParamAndTwoSources(soundEditor.patchingParamSelected, source_selection::regularMenu.s,
	                                            source_selection::rangeMenu.s);
	return paramDescriptor;
}

ParamDescriptor Range::getDestinationDescriptor() {
	ParamDescriptor paramDescriptor;
	paramDescriptor.setToHaveParamAndSource(soundEditor.patchingParamSelected, source_selection::regularMenu.s);
	return paramDescriptor;
}

uint8_t Range::getS() {
	return source_selection::rangeMenu.s;
}

uint8_t Range::shouldBlinkPatchingSourceShortcut(int s, uint8_t* colour) {

	// If this is the actual source we're editing for...
	if (s == getS()) {
		*colour = 0b110;
		return 0;
	}

	// Or, if it's the source whose range we are controlling...
	else if (source_selection::regularMenu.s == s) return 3; // Did I get this right? #patchingoverhaul2021

	else return 255;
}

MenuItem* Range::patchingSourceShortcutPress(int newS, bool previousPressStillActive) {
	return (MenuItem*)0xFFFFFFFF;
}

// FixedPatchCableStrength ----------------------------------------------------------------------------

} // namespace menu_item::patch_cable_strength
