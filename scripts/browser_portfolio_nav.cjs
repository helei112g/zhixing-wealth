/** Open a portfolio by its row after navigating to the returns homepage. */
async function openPortfolio(page, id) {
 await page.locator('[data-portfolio-id="'+id+'"]').getByRole('link').click();
 await page.getByRole('heading',{name:'组合详情',exact:true}).waitFor();
 await page.locator('.loading').waitFor({state:'hidden'});
}

module.exports = {openPortfolio};
